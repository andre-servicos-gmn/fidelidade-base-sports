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

import hmac
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


def _check_path_secret(path_secret: str | None) -> None:
    """Confere o segredo embutido na URL, quando há um configurado.

    Existe porque o App Secret pode não estar disponível (ver o README): a Meta
    só chama uma URL, sem header customizado, então o único segredo que dá para
    exigir dela é o próprio caminho. É a MESMA proteção do webhook da Evolution
    que já roda em produção (segredo compartilhado), não um nível abaixo dela.

    Mais fraco que o HMAC, e por um motivo concreto: um segredo na URL aparece
    em log de acesso, log de proxy e captura de erro, onde uma assinatura nunca
    apareceria. É ponte, não destino — com `META_APP_SECRET` preenchido, o HMAC
    passa a valer junto (ver `_authorize`).

    Responde 404, não 403: para quem erra o caminho, o endpoint simplesmente
    não existe.
    """
    esperado = get_settings().meta_webhook_path_secret
    if not esperado:
        return

    # compare_digest: comparação em tempo constante. `==` sai no primeiro byte
    # diferente, e esse tempo vaza o segredo caractere a caractere.
    if not path_secret or not hmac.compare_digest(path_secret, esperado):
        logger.warning("webhook da Meta recusado: segredo de caminho inválido")
        raise HTTPException(status_code=404, detail="not found")


def _require_some_guard() -> None:
    """FAIL-CLOSED: sem App Secret E sem segredo de caminho, recusa tudo.

    Sem esta trava, esquecer as duas configurações deixaria o endpoint aberto —
    e quem descobrisse a URL poderia forjar mensagens, resgatando cupons e
    atribuindo comissão de afiliado em nome de clientes reais.
    """
    settings = get_settings()
    if not settings.meta_app_secret and not settings.meta_webhook_path_secret:
        logger.error(
            "webhook da Meta sem proteção: configure META_APP_SECRET "
            "(preferido) ou META_WEBHOOK_PATH_SECRET."
        )
        raise HTTPException(status_code=403, detail="webhook not configured")


@router.get("/webhook/meta", response_class=PlainTextResponse)
@router.get("/webhook/meta/{path_secret}", response_class=PlainTextResponse)
async def verify_webhook(
    path_secret: str | None = None,
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> str:
    """Handshake de verificação da URL, feito UMA vez ao cadastrar o webhook.

    A Meta chama este GET com um token e um desafio. Devolvendo o desafio em
    TEXTO PURO com 200, ela aceita a URL; qualquer outra coisa (405, JSON,
    corpo diferente) e o cadastro é recusado.

    O GET não vem assinado — a Meta só assina os POSTs. Então aqui a defesa é o
    segredo do caminho (quando houver) mais o verify token.

    FAIL-CLOSED: com `META_VERIFY_TOKEN` vazio, recusa. Assim uma configuração
    esquecida não vira uma URL que qualquer um consegue registrar.
    """
    _require_some_guard()
    _check_path_secret(path_secret)

    settings = get_settings()
    esperado = settings.meta_verify_token

    if not esperado or hub_mode != "subscribe" or hub_verify_token != esperado:
        logger.warning("verificação do webhook da Meta recusada (token/modo)")
        raise HTTPException(status_code=403, detail="verification failed")

    return hub_challenge or ""


@router.post("/webhook/meta")
@router.post("/webhook/meta/{path_secret}")
async def receive_webhook(
    request: Request,
    path_secret: str | None = None,
    sender: MessageSender = Depends(get_message_sender),
    store: SessionStore = Depends(get_session_store),
    session_factory=Depends(get_session_factory),
) -> dict:
    """Recebe eventos da Cloud API. Responde 200 rápido.

    A assinatura é conferida sobre o CORPO CRU, antes de qualquer parsing — é o
    único jeito de o HMAC bater (ver `signature.py`).
    """
    _require_some_guard()
    _check_path_secret(path_secret)

    settings = get_settings()
    raw = await request.body()

    # DEGRAU AUTOMÁTICO: com App Secret configurado, o HMAC vale SEMPRE — mesmo
    # que o segredo de caminho também esteja ativo (os dois somam, não se
    # substituem). Colar o segredo no `.env` liga a proteção forte sozinho, sem
    # editar código e sem depender de alguém lembrar de reverter a ponte.
    if settings.meta_app_secret and not verify_signature(
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
