"""Envio pela WhatsApp Cloud API (Meta) — outra implementação de MessageSender.

A lógica de conversa não sabe que isto existe: ela só chama `send_text`. Trocar
Evolution por Meta é escolher outra subclasse no factory.

REGRA DA JANELA DE 24 HORAS (a que decide o custo e o que funciona):
- Responder alguém que te escreveu nas últimas 24h -> texto livre, GRATUITO.
  É o caso de 23 das 24 mensagens do bot.
- Iniciar conversa fora dessa janela -> só com TEMPLATE aprovado, e cobrado.
  É o caso da mensagem pós-compra.

`send_text` cobre o primeiro caso. `send_template` existe para o segundo — se
tentar `send_text` fora da janela, a Meta recusa com erro 131047.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.whatsapp.evolution.sender import MessageSender
from app.whatsapp.meta.phone import canonical_to_meta


class MetaCloudSender(MessageSender):
    """Envio via Graph API. Mesmo contrato do EvolutionSender."""

    def __init__(
        self,
        access_token: str,
        phone_number_id: str,
        graph_version: str = "v21.0",
        client: httpx.AsyncClient | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._token = access_token
        self._phone_number_id = phone_number_id
        self._version = graph_version
        self._client = client
        self._timeout = timeout

    @property
    def _url(self) -> str:
        return (
            f"https://graph.facebook.com/{self._version}"
            f"/{self._phone_number_id}/messages"
        )

    async def _post(self, payload: dict[str, Any]) -> None:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        if self._client is not None:
            resp = await self._client.post(self._url, json=payload, headers=headers)
            resp.raise_for_status()
            return
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._url, json=payload, headers=headers)
            resp.raise_for_status()

    async def send_text(self, phone_canonical: str, text: str) -> None:
        """Texto livre. Só vale dentro da janela de 24h aberta pelo cliente."""
        await self._post(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": canonical_to_meta(phone_canonical),
                "type": "text",
                # preview_url=False: link vira texto, sem cartão de pré-visualização.
                "text": {"preview_url": False, "body": text},
            }
        )

    async def send_template(
        self,
        phone_canonical: str,
        template_name: str,
        language: str = "pt_BR",
        body_params: list[str] | None = None,
    ) -> None:
        """Template aprovado — o único jeito de INICIAR conversa fora da janela.

        `body_params` preenche os {{1}}, {{2}}... na ordem em que aparecem no
        texto aprovado. A quantidade tem que bater exatamente com o template
        registrado, senão a Meta recusa a mensagem.
        """
        components = []
        if body_params:
            components.append(
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": str(p)} for p in body_params
                    ],
                }
            )

        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": canonical_to_meta(phone_canonical),
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language},
            },
        }
        if components:
            payload["template"]["components"] = components

        await self._post(payload)
