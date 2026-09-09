"""Estado conversacional por telefone, com TTL.

Interface abstrata + duas implementações, no mesmo padrão da TouchPay
(interface + impl):

- `InMemorySessionStore`: em memória, expiração por inatividade. Só serve para
  1 worker single-process (cada processo tem sua própria memória).
- `RedisSessionStore`: estado compartilhado entre processos/workers, com TTL
  nativo do Redis. Necessário para multi-worker e para o worker de polling em
  processo separado (o estado precisa casar com o webhook). Ver a Fase 3 em
  `docs/fase3-redis-session-store.md`.

Ambos os métodos são assíncronos (casam com o cliente `redis.asyncio`). Quem
escolhe a implementação é `app.dependencies.get_session_store` (por config).
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger("fidelidade.session_store")


class ConversationStep(str, Enum):
    """Passo atual do fluxo de conversa."""

    IDLE = "IDLE"
    # Onboarding do Base Club, nesta ordem: quer participar? -> aceita o
    # regulamento? -> informe o CPF. O consentimento vem ANTES de pedir o CPF
    # de propósito: não se coleta dado pessoal sem o aceite registrado.
    AWAITING_JOIN_CHOICE = "AWAITING_JOIN_CHOICE"
    AWAITING_TERMS_CONSENT = "AWAITING_TERMS_CONSENT"
    AWAITING_CPF = "AWAITING_CPF"
    AWAITING_REWARD_CHOICE = "AWAITING_REWARD_CHOICE"
    # Após uma compra, perguntamos o código de afiliado. `data` carrega
    # {"source_reference": str, "points": int} da compra a ser atribuída, e
    # `code_requested` (bool) marca que o cliente já disse "sim" e o código foi
    # pedido — o passo cobre os dois turnos do fluxo com botões.
    AWAITING_AFFILIATE_CODE = "AWAITING_AFFILIATE_CODE"


@dataclass
class ConversationState:
    """Estado de uma conversa: o passo + dados temporários.

    `data` guarda, por exemplo, a lista de recompensas oferecida, para
    interpretar a escolha "1"/"2"/"3" no passo AWAITING_REWARD_CHOICE.
    """

    step: ConversationStep = ConversationStep.IDLE
    data: dict[str, Any] = field(default_factory=dict)


class SessionStore(ABC):
    """Contrato de armazenamento de estado conversacional."""

    @abstractmethod
    async def get(self, phone: str) -> ConversationState | None:
        """Retorna o estado do telefone, ou None se inexistente/expirado."""
        ...

    @abstractmethod
    async def set(self, phone: str, state: ConversationState) -> None:
        """Grava/atualiza o estado e renova o TTL."""
        ...

    @abstractmethod
    async def delete(self, phone: str) -> None:
        """Remove o estado do telefone (volta ao IDLE/primeiro contato)."""
        ...


class InMemorySessionStore(SessionStore):
    """Implementação em memória com expiração por inatividade (TTL).

    Espelha o "Redis com TTL" do doc de segurança. `clock` é injetável para
    testar a expiração sem esperar tempo real.
    """

    def __init__(
        self,
        ttl_seconds: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._data: dict[str, tuple[ConversationState, float]] = {}

    async def get(self, phone: str) -> ConversationState | None:
        entry = self._data.get(phone)
        if entry is None:
            return None
        state, expires_at = entry
        if self._clock() >= expires_at:
            self._data.pop(phone, None)
            return None
        return state

    async def set(self, phone: str, state: ConversationState) -> None:
        self._data[phone] = (state, self._clock() + self._ttl)

    async def delete(self, phone: str) -> None:
        self._data.pop(phone, None)


# Prefixo das chaves no Redis. Isola o estado de conversa de outras chaves que
# possam existir na mesma instância.
_KEY_PREFIX = "fidelidade:session:"


def _serialize(state: ConversationState) -> str:
    """ConversationState -> JSON. `data` já é JSON-safe (str/int/float/None):
    ver `docs/fase3-redis-session-store.md`. Não precisa de encoder custom."""
    return json.dumps({"step": state.step.value, "data": state.data})


def _deserialize(raw: str) -> ConversationState:
    """JSON -> ConversationState, reconstruindo o enum do passo."""
    payload = json.loads(raw)
    return ConversationState(
        step=ConversationStep(payload["step"]),
        data=payload.get("data") or {},
    )


class RedisSessionStore(SessionStore):
    """Estado conversacional no Redis: compartilhado e com TTL nativo.

    Serializa o estado como JSON e usa `SET key value EX ttl` — o Redis expira a
    chave sozinho por inatividade (cada `set` renova o TTL). Namespace por
    prefixo (`fidelidade:session:{phone}`).

    Política FAIL-SAFE (decidida na Fase 3): se o Redis estiver indisponível, o
    sistema trata a conversa como "sem estado" em vez de estourar erro para o
    cliente:

    - `get`  em falha -> loga e retorna None (a conversa cai no menu/primeiro
      contato). NÃO propaga: o webhook responde 200, não 500.
    - `set`/`delete` em falha -> logam e engolem (não derrubam o webhook).

    Isso NÃO é perda de dado durável: o estado é efêmero por design (conversas
    curtas). O trade-off aceito é o cliente reencontrar o menu numa queda do
    Redis, em vez de ver uma falha.
    """

    def __init__(
        self,
        client: "Redis",
        ttl_seconds: int = 600,
    ) -> None:
        self._client = client
        self._ttl = ttl_seconds

    @staticmethod
    def _key(phone: str) -> str:
        return f"{_KEY_PREFIX}{phone}"

    async def get(self, phone: str) -> ConversationState | None:
        try:
            raw = await self._client.get(self._key(phone))
        except Exception:  # noqa: BLE001 — fail-safe: Redis fora -> sem estado
            logger.exception("Redis get falhou para %s; tratando como sem estado", phone)
            return None
        if raw is None:
            return None
        # `decode_responses=True` no cliente já entrega str; mas toleramos bytes.
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            return _deserialize(raw)
        except Exception:  # noqa: BLE001 — valor corrompido não pode derrubar o fluxo
            logger.exception("estado corrompido no Redis para %s; ignorando", phone)
            return None

    async def set(self, phone: str, state: ConversationState) -> None:
        try:
            await self._client.set(
                self._key(phone), _serialize(state), ex=self._ttl
            )
        except Exception:  # noqa: BLE001 — fail-safe: não derruba o webhook
            logger.exception("Redis set falhou para %s; estado não persistido", phone)

    async def delete(self, phone: str) -> None:
        try:
            await self._client.delete(self._key(phone))
        except Exception:  # noqa: BLE001 — fail-safe
            logger.exception("Redis delete falhou para %s", phone)
