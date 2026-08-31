"""Webhook da WhatsApp Cloud API (Meta), em caminho PRÓPRIO: /webhook/meta.

Por que não reaproveitar /webhook/whatsapp: aquele é da Evolution, está em
produção e funcionando. Os dois formatos são incompatíveis — autenticação
diferente (header próprio × assinatura HMAC) e payload diferente. Caminhos
separados deixam os dois canais conviverem: dá para cadastrar e testar a Meta
sem derrubar o que já roda, e voltar atrás sem downtime.

A lógica de conversa é EXATAMENTE a mesma (`handle_message`). Este módulo é só
tradução de fronteira.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from app.config import get_settings
from app.dependencies import (
    get_message_sender,
    get_session_factory,
    get_session_store,
)
from app.whatsapp.conversation import handle_message
from app.whatsapp.evolution.sender import MessageSender
from app.whatsapp.meta.phone import meta_to_canonical
from app.whatsapp.meta.signature import verify_signature
from app.whatsapp.meta.webhook_schema import MetaWebhook
from app.whatsapp.session_store import SessionStore

logger = logging.getLogger("fidelidade.meta_webhook")

router = APIRouter()

SIGNATURE_HEADER = "X-Hub-Signature-256"


@router.get("/webhook/meta", response_class=PlainTextResponse)
async def verify_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> str:
    """Handshake de verificação da URL, feito UMA vez ao cadastrar o webhook.

    A Meta chama este GET com um token e um desafio. Devolvendo o desafio em
    TEXTO PURO com 200, ela aceita a URL; qualquer outra coisa (405, JSON,
    corpo diferente) e o cadastro é recusado.

    FAIL-CLOSED: com `META_VERIFY_TOKEN` vazio, recusa. Assim uma configuração
    esquecida não vira uma URL que qualquer um consegue registrar.
    """
    settings = get_settings()
    esperado = settings.meta_verify_token

    if not esperado or hub_mode != "subscribe" or hub_verify_token != esperado:
        logger.warning("verificação do webhook da Meta recusada (token/modo)")
        raise HTTPException(status_code=403, detail="verification failed")

    return hub_challenge or ""


@router.post("/webhook/meta")
async def receive_webhook(
    request: Request,
    sender: MessageSender = Depends(get_message_sender),
    store: SessionStore = Depends(get_session_store),
    session_factory=Depends(get_session_factory),
) -> dict:
    """Recebe eventos da Cloud API. Responde 200 rápido.

    A assinatura é conferida sobre o CORPO CRU, antes de qualquer parsing — é o
    único jeito de o HMAC bater (ver `signature.py`).
    """
    settings = get_settings()
    raw = await request.body()

    if not verify_signature(
        settings.meta_app_secret, raw, request.headers.get(SIGNATURE_HEADER)
    ):
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        payload = MetaWebhook.model_validate_json(raw)
    except Exception:  # noqa: BLE001 — payload malformado não derruba o webhook
        logger.exception("payload da Meta ilegível; ignorando")
        return {"status": "ignored"}

    # A Meta AGRUPA eventos: um POST pode trazer várias mensagens, de clientes
    # diferentes. Todas precisam ser atendidas — o 200 faz ela considerar o
    # lote inteiro entregue, sem reenvio.
    incoming = payload.extract_text_messages()
    if not incoming:
        # Status de entrega, mídia, reação: nada a fazer.
        return {"status": "ignored"}

    replies_total = 0
    sent = 0
    for mensagem in incoming:
        phone_canonical = meta_to_canonical(mensagem.from_number)
        if not phone_canonical:
            continue

        try:
            replies = await handle_message(
                phone_canonical, mensagem.text, store, session_factory
            )
        except Exception:  # noqa: BLE001 — uma conversa ruim não mata o lote
            logger.exception(
                "falha ao processar mensagem de %s; seguindo com o lote",
                phone_canonical,
            )
            continue

        replies_total += len(replies)

        # Falha de ENVIO não pode virar 500: a Meta reentregaria o evento e o
        # cliente receberia tudo duas vezes. Mesma política do webhook da
        # Evolution.
        for reply in replies:
            try:
                await sender.send_text(phone_canonical, reply)
                sent += 1
            except Exception:  # noqa: BLE001 — ver comentário acima
                logger.exception(
                    "falha ao enviar resposta para %s; seguindo sem derrubar "
                    "o webhook",
                    phone_canonical,
                )

    return {
        "status": "ok",
        "messages": len(incoming),
        "replies": replies_total,
        "sent": sent,
    }
