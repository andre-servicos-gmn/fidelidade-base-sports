"""Envio de mensagens atrás de uma interface (mesmo padrão do TouchPay).

A lógica de conversa NÃO conhece HTTP nem Evolution. Trocar o provedor é só
fornecer outra subclasse de `MessageSender`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import httpx

from app.whatsapp.evolution.phone import canonical_to_evolution


class MessageSender(ABC):
    """Contrato de envio de mensagem de texto para um telefone canônico."""

    @abstractmethod
    async def send_text(self, phone_canonical: str, text: str) -> None:
        """Envia `text` para o telefone (no formato canônico da Fase 5)."""
        ...


class EvolutionSender(MessageSender):
    """Envio real via Evolution API (httpx).

    Converte o telefone canônico para o formato Evolution e chama o endpoint
    de envio de texto.

    NOTA / TODO (contrato pode variar por versão da Evolution): usamos
        POST {base_url}/message/sendText/{instance}
        headers: {"apikey": <evolution_api_key>}
        json:    {"number": "5511987654321", "text": "..."}
    Versões mais antigas usam `{"textMessage": {"text": ...}}`. Se a sua
    instância divergir, ajuste só aqui — o resto do sistema não muda.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        instance: str,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._instance = instance
        self._client = client
        self._timeout = timeout

    async def send_text(self, phone_canonical: str, text: str) -> None:
        url = f"{self._base_url}/message/sendText/{self._instance}"
        payload = {
            "number": canonical_to_evolution(phone_canonical),
            "text": text,
        }
        headers = {"apikey": self._api_key}

        if self._client is not None:
            resp = await self._client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()


class MockMessageSender(MessageSender):
    """Não faz I/O: registra os envios numa lista para inspeção em testes."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_text(self, phone_canonical: str, text: str) -> None:
        self.sent.append((phone_canonical, text))
