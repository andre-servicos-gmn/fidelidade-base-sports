"""Webhook da WhatsApp Cloud API (Meta), em caminho PRÓPRIO: /webhook/meta.

Por que não reaproveitar /webhook/whatsapp: aquele é da Evolution, está em
produção e funcionando. Os dois formatos são incompatíveis — autenticação
diferente (header próprio × assinatura HMAC) e payload diferente. Caminhos
separados deixam os dois canais conviverem: dá para cadastrar e testar a Meta
sem derrubar o que já roda, e voltar atrás sem downtime.

A lógica de conversa é EXATAMENTE a mesma (`handle_message`). Este módulo é só
tradução de fronteira — mais a separação dos toques do sistema de boas-vindas,
que divide o número com este (ver `app.whatsapp.meta.boasvindas`).
"""

from __future__ import annotations

import hmac
import logging

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import PlainTextResponse

from app.config import get_settings
from app.dependencies import (
    get_message_sender,
    get_session_factory,
    get_session_store,
)
from app.whatsapp.conversation import handle_message
from app.whatsapp.evolution.sender import MessageSender
from app.whatsapp.meta.boasvindas import (
    contains_boasvindas_tap,
    is_boasvindas_payload,
    normalize_payloads,
    repassar,
)
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
    background_tasks: BackgroundTasks,
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

    # BOAS-VINDAS: o outro sistema da loja usa este mesmo número e pede
    # consentimento por template com botões. A Meta só chama a nós, então o
    # evento com esse toque é repassado a ele.
    # - Só DEPOIS da autenticação: nunca repassar o que nós mesmos recusaríamos.
    # - Só o lote que tem o toque: as conversas com o robô de fidelidade não
    #   saem daqui (minimização).
    # - Em BackgroundTasks: roda depois do 200, então um boas-vindas lento ou
    #   fora do ar nunca atrasa a resposta — e a Meta reenvia o que demora.
    payloads_boasvindas = normalize_payloads(settings.boasvindas_button_payloads)
    repassado = False
    if contains_boasvindas_tap(payload, payloads_boasvindas):
        if settings.boasvindas_forward_url.strip():
            background_tasks.add_task(
                repassar,
                settings.boasvindas_forward_url,
                raw,  # bytes CRUS: a assinatura só bate sobre eles
                request.headers,
                settings.boasvindas_forward_timeout_seconds,
            )
            repassado = True
        else:
            # O toque vai ser descartado (fica fora da conversa, logo abaixo) e
            # não sai daqui. Sem esta linha, variável vazia ou com nome errado
            # no painel fica igual a "a Meta nunca chamou", e um NAO_ACEITO
            # (revogação) some sem rastro. Sem telefone, sem URL.
            logger.warning(
                "toque do boas-vindas descartado: BOASVINDAS_FORWARD_URL vazio"
            )

    # A Meta AGRUPA eventos: um POST pode trazer várias mensagens, de clientes
    # diferentes. Todas precisam ser atendidas — o 200 faz ela considerar o
    # lote inteiro entregue, sem reenvio.
    #
    # Menos os toques do boas-vindas, com o repasse ligado ou NÃO: o botão
    # "Aceito" é consentimento para a saudação por voz, não resposta a este
    # robô. Na conversa, "aceito" é um "sim" do onboarding — e contaria como
    # aceite do regulamento da fidelidade. As demais mensagens do lote seguem.
    incoming = [
        mensagem
        for mensagem in payload.extract_text_messages()
        if not is_boasvindas_payload(mensagem.button_payload, payloads_boasvindas)
    ]
    if not incoming:
        # Status de entrega, mídia, reação, toque do boas-vindas: nada a fazer
        # aqui. "forwarded" só diz que o repasse foi AGENDADO; o resultado dele
        # sai no log, porque roda depois desta resposta.
        return {"status": "forwarded" if repassado else "ignored"}

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
