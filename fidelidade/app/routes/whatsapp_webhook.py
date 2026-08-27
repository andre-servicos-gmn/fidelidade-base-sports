"""Endpoint do webhook do WhatsApp (Evolution API).

Adapter fino: valida o token, normaliza o número na fronteira, chama
`handle_message` (Fase 5) e envia as respostas via `MessageSender`. Nenhuma
regra de negócio aqui.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException

from app.config import get_settings
from app.dependencies import (
    get_message_sender,
    get_session_factory,
    get_session_store,
)
from app.whatsapp.conversation import handle_message
from app.whatsapp.evolution.phone import evolution_to_canonical
from app.whatsapp.evolution.sender import MessageSender
from app.whatsapp.evolution.webhook_schema import EvolutionWebhook
from app.whatsapp.session_store import SessionStore

logger = logging.getLogger("fidelidade.webhook")

router = APIRouter()

# Header que carrega o segredo do webhook. O VALOR esperado vem da config
# (`webhook_token`); a requisição sem o valor certo é rejeitada com 401.
WEBHOOK_TOKEN_HEADER = "X-Webhook-Token"


@router.post("/webhook/whatsapp")
async def whatsapp_webhook(
    payload: EvolutionWebhook,
    x_webhook_token: str | None = Header(default=None, alias=WEBHOOK_TOKEN_HEADER),
    sender: MessageSender = Depends(get_message_sender),
    store: SessionStore = Depends(get_session_store),
    session_factory=Depends(get_session_factory),
) -> dict:
    """Recebe eventos da Evolution. Responde 200 rápido.

    Processamento é síncrono por enquanto (volume baixo). Se ficar pesado,
    responder 200 e processar em background (ex: fila/asyncio.create_task).
    """
    settings = get_settings()
    # Autenticação: fail-closed. Token de config vazio também rejeita.
    if not settings.webhook_token or x_webhook_token != settings.webhook_token:
        raise HTTPException(status_code=401, detail="invalid webhook token")

    incoming = payload.extract_text_message()
    if incoming is None:
        # Status, mídia, mensagem do próprio bot, etc.: ignora sem erro.
        return {"status": "ignored"}

    phone_canonical = evolution_to_canonical(incoming.remote_jid)
    if not phone_canonical:
        return {"status": "ignored"}

    replies = await handle_message(
        phone_canonical, incoming.text, store, session_factory
    )

    # Falha de ENVIO não pode virar 500. Se o webhook devolve erro, a Evolution
    # REENTREGA o evento e a mensagem seria reprocessada (respostas duplicadas,
    # loop de retry). Causas comuns e esperadas: número inexistente no WhatsApp,
    # cliente bloqueou o bot, instabilidade momentânea da Evolution.
    # Registramos a falha e seguimos: o estado da conversa já foi atualizado.
    sent = 0
    for reply in replies:
        try:
            await sender.send_text(phone_canonical, reply)
            sent += 1
        except Exception:  # noqa: BLE001 — ver comentário acima
            logger.exception(
                "falha ao enviar resposta para %s; seguindo sem derrubar o webhook",
                phone_canonical,
            )

    return {"status": "ok", "replies": len(replies), "sent": sent}
