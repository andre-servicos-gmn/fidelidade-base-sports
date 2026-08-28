"""Dependências compartilhadas do app (injetáveis e sobreponíveis em testes).

Todas via `@lru_cache` para serem SINGLETONS no processo:

- `get_session_store`: o estado conversacional precisa sobreviver ENTRE
  requisições (cada webhook é um POST separado). Escolhido por config: em
  memória (1 worker) ou Redis (`use_redis_session_store=true`, compartilhado
  entre processos/workers). Ver `session_store.py` e a seção "Estado de
  conversa" no README.
- `get_message_sender`: escolhido por config (mock vs Evolution real), mesmo
  padrão do factory do TouchPay.
- `get_session_factory`: a fábrica de `AsyncSession` (uma sessão nova por
  requisição). Sobreposta nos testes para apontar a um engine de teste.

Nos testes, use `app.dependency_overrides[...]` para injetar mocks.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.base import get_sessionmaker
from app.whatsapp.evolution.sender import (
    EvolutionSender,
    MessageSender,
    MockMessageSender,
)
from app.whatsapp.session_store import (
    InMemorySessionStore,
    RedisSessionStore,
    SessionStore,
)


@lru_cache
def get_redis_client():
    """Cliente `redis.asyncio` singleton (criado a partir de `redis_url`).

    Só é chamado quando `use_redis_session_store=true`. Importa o `redis`
    localmente para não obrigar quem roda em memória a ter a lib carregada.
    """
    from redis.asyncio import Redis

    settings = get_settings()
    if not settings.redis_url:
        raise RuntimeError(
            "USE_REDIS_SESSION_STORE=true exige REDIS_URL configurada "
            "(ex: rediss://default:[SENHA]@[HOST].upstash.io:6379)."
        )
    # decode_responses=True: o store trabalha com str, não bytes.
    return Redis.from_url(settings.redis_url, decode_responses=True)


@lru_cache
def get_session_store() -> SessionStore:
    """Store do estado de conversa: Redis (compartilhado) ou memória (1 worker).

    Escolhido por config. Default seguro (`use_redis_session_store=false`):
    `InMemorySessionStore`, que só serve para 1 worker single-process. Ligue o
    Redis para multi-worker ou worker de polling em processo separado.
    """
    settings = get_settings()
    if settings.use_redis_session_store:
        return RedisSessionStore(
            get_redis_client(), ttl_seconds=settings.session_ttl_seconds
        )
    return InMemorySessionStore(ttl_seconds=settings.session_ttl_seconds)


async def close_redis_client() -> None:
    """Fecha o pool do Redis no shutdown (chamado pelo lifespan da API).

    Idempotente e tolerante: se o cliente nunca foi criado (memória) ou já foi
    fechado, não faz nada.
    """
    if get_redis_client.cache_info().currsize == 0:
        return
    client = get_redis_client()
    try:
        await client.aclose()
    except Exception:  # noqa: BLE001 — shutdown não pode falhar por isso
        pass


@lru_cache
def get_message_sender() -> MessageSender:
    """Escolhe por quem as mensagens SAEM: mock, Cloud API (Meta) ou Evolution.

    Ordem de precedência, do mais seguro ao mais real:
      1. `use_mock_whatsapp=true`  -> não envia nada (dev e testes).
      2. `use_meta_whatsapp=true`  -> Cloud API oficial da Meta.
      3. caso contrário            -> Evolution (Baileys).

    O RECEBIMENTO é independente disto: os webhooks da Evolution e da Meta têm
    caminhos próprios e podem ficar ativos ao mesmo tempo. Assim dá para migrar
    o envio sem parar de receber, e voltar atrás trocando uma variável.
    """
    settings = get_settings()
    if settings.use_mock_whatsapp:
        return MockMessageSender()

    if settings.use_meta_whatsapp:
        from app.whatsapp.meta.sender import MetaCloudSender

        return MetaCloudSender(
            access_token=settings.meta_access_token,
            phone_number_id=settings.meta_phone_number_id,
            graph_version=settings.meta_graph_version,
        )

    return EvolutionSender(
        base_url=settings.evolution_base_url,
        api_key=settings.evolution_api_key,
        instance=settings.evolution_instance,
    )


def get_session_factory():
    """Fábrica de sessão async (singleton via cache do engine em db.base).

    Exposta como dependency para ser sobreposta nos testes (engine de teste).
    """
    return get_sessionmaker()


async def get_db(
    session_factory=Depends(get_session_factory),
) -> AsyncIterator[AsyncSession]:
    """Yields uma AsyncSession nova por requisição (e a fecha ao final).

    FastAPI cacheia esta dependency por requisição, então auth + rota
    compartilham a MESMA sessão. Sobreponha `get_session_factory` nos testes.
    """
    async with session_factory() as session:
        yield session
