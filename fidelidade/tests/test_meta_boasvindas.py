"""Convivência com o sistema de boas-vindas no mesmo número (Cloud API).

Puros: sem banco e sem rede (o "outro sistema" é um `httpx.MockTransport`).

O que estes testes travam:

- `repassar` manda os bytes EXATOS, só os cabeçalhos necessários mais a marca
  de repasse, e nunca levanta — nem com o outro sistema fora do ar;
- a rota repassa SÓ o lote com toque do boas-vindas, nos dois formatos de
  botão da Meta, e só depois da autenticação;
- o toque do boas-vindas nunca chega a `handle_message`, com o repasse ligado
  ou não, enquanto as outras mensagens do mesmo lote seguem normalmente.

A URL usada é fictícia (`.invalid`): a real carrega o segredo do outro sistema
e nunca entra em código.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.dependencies import (
    get_message_sender,
    get_session_factory,
    get_session_store,
)
from app.main import app
from app.routes import meta_webhook
from app.whatsapp.evolution.sender import MockMessageSender
from app.whatsapp.meta import boasvindas
from app.whatsapp.meta.boasvindas import (
    contains_boasvindas_tap,
    normalize_payloads,
    repassar,
)
from app.whatsapp.meta.webhook_schema import MetaWebhook
from app.whatsapp.session_store import InMemorySessionStore

VERIFY_TOKEN = "token-de-verificacao-de-teste"
APP_SECRET = "segredo-do-app-de-teste"
PATH_SECRET = "caminho-secreto-de-teste"
FORWARD_URL = "https://exemplo.invalid/api/v1/whatsapp/webhook/segredo-de-teste"
PATH = "/webhook/meta"
PAYLOADS = normalize_payloads("ACEITO,NAO_ACEITO")


# --------------------------------------------------------------------------- #
# Montagem de payloads da Meta                                                 #
# --------------------------------------------------------------------------- #
def _envelope(*messages: dict, statuses: list | None = None) -> dict:
    value: dict = {"messaging_product": "whatsapp"}
    if messages:
        value["contacts"] = [{"profile": {"name": "Cliente"}}]
        value["messages"] = list(messages)
    if statuses is not None:
        value["statuses"] = statuses
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "1", "changes": [{"field": "messages", "value": value}]}],
    }


def _template_button(payload: str, text: str = "Aceito", frm: str = "5511900000001") -> dict:
    """Toque em botão de TEMPLATE: campo `button` de primeiro nível."""
    return {
        "from": frm,
        "id": "wamid.BOTAO",
        "timestamp": "1",
        "type": "button",
        "button": {"text": text, "payload": payload},
    }


def _interactive_button(reply_id: str, frm: str = "5511900000001") -> dict:
    """Toque em botão INTERATIVO: `interactive.button_reply`."""
    return {
        "from": frm,
        "id": "wamid.INTERATIVO",
        "timestamp": "1",
        "type": "interactive",
        "interactive": {
            "type": "button_reply",
            "button_reply": {"id": reply_id, "title": "Aceito"},
        },
    }


def _text(body: str, frm: str = "5511900000002") -> dict:
    return {
        "from": frm,
        "id": "wamid.TEXTO",
        "timestamp": "1",
        "type": "text",
        "text": {"body": body},
    }


def _sign(body: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# --------------------------------------------------------------------------- #
# Reconhecimento do toque                                                      #
# --------------------------------------------------------------------------- #
def test_normalize_payloads_ignores_case_spaces_and_blanks():
    assert normalize_payloads(" aceito , Nao_Aceito,, ") == frozenset(
        {"ACEITO", "NAO_ACEITO"}
    )
    assert normalize_payloads("") == frozenset()
    assert normalize_payloads(None) == frozenset()


@pytest.mark.parametrize(
    "mensagem",
    [
        _template_button("ACEITO"),
        _template_button("NAO_ACEITO", text="Não aceito"),
        _template_button(" aceito "),
        _interactive_button("NAO_ACEITO"),
        _interactive_button("aceito"),
    ],
)
def test_boasvindas_tap_is_recognized_in_both_formats(mensagem):
    assert contains_boasvindas_tap(MetaWebhook.model_validate(_envelope(mensagem)), PAYLOADS)


@pytest.mark.parametrize(
    "webhook",
    [
        # "aceito" DIGITADO é conversa daqui, não toque no botão.
        _envelope(_text("aceito")),
        # Botão do template de afiliado deste sistema.
        _envelope(_template_button("SIM", text="Sim, tenho o código")),
        # Botão interativo do menu deste sistema.
        _envelope(_interactive_button("2")),
        # Status de entrega.
        _envelope(statuses=[{"status": "delivered"}]),
    ],
)
def test_other_events_are_not_boasvindas_taps(webhook):
    assert not contains_boasvindas_tap(MetaWebhook.model_validate(webhook), PAYLOADS)


def test_empty_payload_list_recognizes_nothing():
    webhook = MetaWebhook.model_validate(_envelope(_template_button("ACEITO")))
    assert not contains_boasvindas_tap(webhook, frozenset())


# --------------------------------------------------------------------------- #
# repassar                                                                     #
# --------------------------------------------------------------------------- #
class _OutroSistema:
    """Faz o papel do boas-vindas: grava o que recebe e responde `status`."""

    def __init__(self, status: int = 200, erro: Exception | None = None) -> None:
        self.status = status
        self.erro = erro
        self.recebidos: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.recebidos.append(request)
        if self.erro is not None:
            raise self.erro
        return httpx.Response(self.status)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


async def test_repassar_sends_identical_bytes_and_only_needed_headers():
    """Bytes EXATOS (espaços, ordem, acento) e só os cabeçalhos da lista.

    Reserializar mudaria o digest da assinatura. Cookie, Authorization e IP de
    origem são do nosso transporte e não podem vazar para o outro sistema.
    """
    corpo = '{"entry":  [ ],"nome":"João"}'.encode()
    entrada = {
        "Content-Type": "application/json; charset=utf-8",
        "X-Hub-Signature-256": "sha256=abc",
        "X-Hub-Signature": "sha1=def",
        "Cookie": "sessao=nao-repassar",
        "Authorization": "Bearer nao-repassar",
        "X-Forwarded-For": "203.0.113.9",
    }
    outro = _OutroSistema()

    async with outro.client() as client:
        resultado = await repassar(FORWARD_URL, corpo, entrada, 3.0, client=client)

    assert resultado == "ok"
    assert len(outro.recebidos) == 1
    enviado = outro.recebidos[0]
    assert enviado.method == "POST"
    assert str(enviado.url) == FORWARD_URL
    assert enviado.content == corpo
    assert enviado.headers["content-type"] == "application/json; charset=utf-8"
    assert enviado.headers["x-hub-signature-256"] == "sha256=abc"
    assert enviado.headers["x-hub-signature"] == "sha1=def"
    assert enviado.headers["x-boasvindas-repassado"] == "nouva-fidelidade"
    for vazado in ("cookie", "authorization", "x-forwarded-for"):
        assert vazado not in enviado.headers


async def test_repassar_defaults_content_type_to_json():
    outro = _OutroSistema()
    async with outro.client() as client:
        await repassar(FORWARD_URL, b"{}", {}, 3.0, client=client)

    assert outro.recebidos[0].headers["content-type"] == "application/json"
    assert "x-hub-signature-256" not in outro.recebidos[0].headers


@pytest.mark.parametrize("marca", ["X-Boasvindas-Repassado", "x-boasvindas-repassado"])
async def test_repassar_never_forwards_a_forward(marca):
    """Trava de laço: o que já chegou repassado não volta, seja qual for a caixa."""
    outro = _OutroSistema()
    async with outro.client() as client:
        resultado = await repassar(
            FORWARD_URL, b"{}", {marca: "boas-vindas"}, 3.0, client=client
        )

    assert resultado == "laco_evitado"
    assert outro.recebidos == []


@pytest.mark.parametrize("url", ["", "   ", None])
async def test_repassar_is_off_without_url(url):
    outro = _OutroSistema()
    async with outro.client() as client:
        resultado = await repassar(url, b"{}", {}, 3.0, client=client)

    assert resultado == "off"
    assert outro.recebidos == []


@pytest.mark.parametrize("status", [302, 404, 500, 503])
async def test_repassar_reports_http_failures(status):
    outro = _OutroSistema(status=status)
    async with outro.client() as client:
        resultado = await repassar(FORWARD_URL, b"{}", {}, 3.0, client=client)

    assert resultado == f"http_{status}"


@pytest.mark.parametrize(
    "erro",
    [
        httpx.ConnectError("conexao recusada"),
        httpx.ReadTimeout("demorou"),
        RuntimeError("qualquer outra coisa"),
    ],
)
async def test_repassar_never_raises_and_never_logs_the_url(erro, caplog):
    """Outro sistema fora do ar vira rótulo, nunca exceção — e a URL não vaza.

    A URL carrega o segredo do boas-vindas; o log tem só o tipo do erro.
    """
    outro = _OutroSistema(erro=erro)
    with caplog.at_level(logging.DEBUG):
        async with outro.client() as client:
            resultado = await repassar(FORWARD_URL, b"{}", {}, 3.0, client=client)

    assert resultado == "erro"
    assert "segredo-de-teste" not in caplog.text
    assert "exemplo.invalid" not in caplog.text


async def test_repassar_closes_only_the_client_it_created(monkeypatch):
    """Cliente próprio é fechado; o recebido de fora continua com quem o deu."""
    criados: list[httpx.AsyncClient] = []
    outro = _OutroSistema()
    original = httpx.AsyncClient

    def _fabrica(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(outro)
        client = original(*args, **kwargs)
        criados.append(client)
        return client

    monkeypatch.setattr(boasvindas.httpx, "AsyncClient", _fabrica)
    assert await repassar(FORWARD_URL, b"{}", {}, 3.0) == "ok"
    assert len(criados) == 1
    assert criados[0].is_closed

    monkeypatch.setattr(boasvindas.httpx, "AsyncClient", original)
    externo = outro.client()
    await repassar(FORWARD_URL, b"{}", {}, 3.0, client=externo)
    assert not externo.is_closed
    await externo.aclose()


# --------------------------------------------------------------------------- #
# Rota: o que é repassado, o que chega à conversa                              #
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    """Postura só-HMAC, repasse ligado para uma URL fictícia."""
    monkeypatch.setenv("META_VERIFY_TOKEN", VERIFY_TOKEN)
    monkeypatch.setenv("META_APP_SECRET", APP_SECRET)
    monkeypatch.setenv("META_WEBHOOK_PATH_SECRET", "")
    monkeypatch.setenv("BOASVINDAS_FORWARD_URL", FORWARD_URL)
    monkeypatch.setenv("BOASVINDAS_FORWARD_TIMEOUT_SECONDS", "3")
    # Explícito: um valor diferente no `.env` do desenvolvedor não pode mudar
    # o que estes testes significam.
    monkeypatch.setenv("BOASVINDAS_BUTTON_PAYLOADS", "ACEITO,NAO_ACEITO")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def outro_sistema(monkeypatch) -> _OutroSistema:
    """O `repassar` REAL, com o transporte trocado pelo boas-vindas de mentira."""
    outro = _OutroSistema()

    async def _repassar(url, raw_body, headers, timeout_seconds, client=None):
        async with outro.client() as proprio:
            return await repassar(url, raw_body, headers, timeout_seconds, client=proprio)

    monkeypatch.setattr(meta_webhook, "repassar", _repassar)
    return outro


@pytest.fixture
def conversa(monkeypatch) -> list[tuple[str, str]]:
    """Registra o que chega a `handle_message` (sem banco)."""
    chamadas: list[tuple[str, str]] = []

    async def _handle(phone, text, store, session_factory):
        chamadas.append((phone, text))
        return ["resposta do robô"]

    monkeypatch.setattr(meta_webhook, "handle_message", _handle)
    return chamadas


@pytest.fixture
def client():
    """App com as dependências de conversa neutralizadas.

    A conversa é substituída pela fixture `conversa`; se algo aqui chegar ao
    banco, o teste quebra em vez de tentar abrir conexão de verdade.
    """

    @asynccontextmanager
    async def _sem_banco():
        raise AssertionError("nenhum teste daqui deveria chegar ao banco")
        yield  # pragma: no cover

    app.dependency_overrides[get_message_sender] = lambda: MockMessageSender()
    app.dependency_overrides[get_session_store] = lambda: InMemorySessionStore()
    app.dependency_overrides[get_session_factory] = lambda: _sem_banco
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _post(client, payload: dict, path: str = PATH, *, assinar: bool = True, extra=None):
    raw = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if assinar:
        headers["X-Hub-Signature-256"] = _sign(raw)
    headers.update(extra or {})
    return raw, client.post(path, content=raw, headers=headers)


@pytest.mark.parametrize(
    "mensagem",
    [
        _template_button("ACEITO"),
        _template_button("NAO_ACEITO", text="Não aceito"),
        _template_button("  aceito "),
        _interactive_button("ACEITO"),
        _interactive_button("nao_aceito"),
    ],
)
def test_route_forwards_boasvindas_tap_and_keeps_it_out_of_the_conversation(
    client, outro_sistema, conversa, mensagem
):
    raw, r = _post(client, _envelope(mensagem))

    assert r.status_code == 200
    assert r.json() == {"status": "forwarded"}
    # Chegou ao boas-vindas, byte a byte, com a assinatura original.
    assert len(outro_sistema.recebidos) == 1
    enviado = outro_sistema.recebidos[0]
    assert enviado.content == raw
    assert enviado.headers["x-hub-signature-256"] == _sign(raw)
    assert enviado.headers["x-boasvindas-repassado"] == "nouva-fidelidade"
    # E NÃO virou conversa: "Aceito" seria um "sim" do onboarding daqui.
    assert conversa == []


@pytest.mark.parametrize(
    "payload",
    [
        _envelope(_text("aceito")),
        _envelope(_template_button("SIM", text="Sim, tenho o código")),
        _envelope(_interactive_button("2")),
    ],
)
def test_route_does_not_forward_this_systems_conversation(
    client, outro_sistema, conversa, payload
):
    """Texto, botão do afiliado e menu são daqui: seguem para a conversa e só."""
    _, r = _post(client, payload)

    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert outro_sistema.recebidos == []
    assert len(conversa) == 1


def test_route_keeps_affiliate_button_label_as_conversation_text(
    client, outro_sistema, conversa
):
    """O botão do template de afiliado continua chegando pelo RÓTULO."""
    _post(client, _envelope(_template_button("SIM", text="Sim, tenho o código")))

    assert conversa == [("11900000001", "Sim, tenho o código")]


def test_route_does_not_forward_status_events(client, outro_sistema, conversa):
    _, r = _post(client, _envelope(statuses=[{"status": "read"}]))

    assert r.status_code == 200
    assert r.json() == {"status": "ignored"}
    assert outro_sistema.recebidos == []
    assert conversa == []


def test_route_does_not_forward_with_bad_or_missing_signature(
    client, outro_sistema, conversa
):
    """Autenticação primeiro: nunca repassar o que nós mesmos recusamos."""
    payload = _envelope(_template_button("ACEITO"))

    _, sem = _post(client, payload, assinar=False)
    raw = json.dumps(payload).encode()
    errada = client.post(
        PATH,
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(raw, "outro-segredo"),
        },
    )

    assert sem.status_code == 401
    assert errada.status_code == 401
    assert outro_sistema.recebidos == []
    assert conversa == []


def test_route_does_not_forward_with_wrong_path_secret(
    client, outro_sistema, conversa, monkeypatch
):
    """Postura sem App Secret: o caminho é a guarda, e errar é 404 sem repasse."""
    monkeypatch.setenv("META_APP_SECRET", "")
    monkeypatch.setenv("META_WEBHOOK_PATH_SECRET", PATH_SECRET)
    get_settings.cache_clear()
    payload = _envelope(_template_button("ACEITO"))

    _, errado = _post(client, payload, f"{PATH}/chute-errado", assinar=False)
    _, nu = _post(client, payload, PATH, assinar=False)
    assert errado.status_code == 404
    assert nu.status_code == 404
    assert outro_sistema.recebidos == []

    # Com o caminho certo, o mesmo evento passa e é repassado.
    _, certo = _post(client, payload, f"{PATH}/{PATH_SECRET}", assinar=False)
    assert certo.status_code == 200
    assert len(outro_sistema.recebidos) == 1


def test_mixed_batch_tap_is_filtered_and_other_message_is_answered(
    client, outro_sistema, conversa
):
    """Lote misto: o toque vai ao boas-vindas, o texto do outro cliente fica aqui."""
    payload = _envelope(
        _template_button("ACEITO", frm="5511900000001"),
        _text("saldo", frm="5511900000002"),
    )

    raw, r = _post(client, payload)

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["messages"] == 1
    assert conversa == [("11900000002", "saldo")]
    assert len(outro_sistema.recebidos) == 1
    assert outro_sistema.recebidos[0].content == raw


def test_without_forward_url_nothing_is_sent_and_tap_is_still_filtered(
    client, outro_sistema, conversa, monkeypatch
):
    """Repasse desligado não reabre a porta: o toque continua fora da conversa."""
    monkeypatch.setenv("BOASVINDAS_FORWARD_URL", "")
    get_settings.cache_clear()

    _, r = _post(client, _envelope(_template_button("ACEITO")))

    assert r.status_code == 200
    assert r.json() == {"status": "ignored"}
    assert outro_sistema.recebidos == []
    assert conversa == []


def test_route_does_not_bounce_an_event_that_came_forwarded(
    client, outro_sistema, conversa
):
    """Se o boas-vindas repassar para cá por engano, o evento não volta."""
    _, r = _post(
        client,
        _envelope(_template_button("ACEITO")),
        extra={"X-Boasvindas-Repassado": "boas-vindas"},
    )

    assert r.status_code == 200
    assert outro_sistema.recebidos == []
    assert conversa == []
