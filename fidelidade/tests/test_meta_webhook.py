"""Testes do webhook da Cloud API (Meta): verificação, assinatura e payload.

Puros: não tocam banco nem rede. O que se testa aqui é a FRONTEIRA — o que a
Meta manda e o que devolvemos. A lógica de conversa tem testes próprios.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.dependencies import (
    get_message_sender,
    get_session_factory,
    get_session_store,
)
from app.main import app
from app.whatsapp.evolution.sender import MockMessageSender
from app.whatsapp.meta.phone import canonical_to_meta, meta_to_canonical
from app.whatsapp.meta.signature import verify_signature
from app.whatsapp.meta.webhook_schema import MetaWebhook
from app.whatsapp.session_store import InMemorySessionStore

VERIFY_TOKEN = "token-de-verificacao-de-teste"
APP_SECRET = "segredo-do-app-de-teste"
PATH = "/webhook/meta"


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    """Aponta a config para valores de teste e limpa o cache do get_settings."""
    monkeypatch.setenv("META_VERIFY_TOKEN", VERIFY_TOKEN)
    monkeypatch.setenv("META_APP_SECRET", APP_SECRET)
    # Este arquivo cobre a postura SÓ-HMAC, no caminho nu /webhook/meta. Sem
    # zerar isto, um segredo de caminho no `.env` do desenvolvedor vazaria para
    # cá (o Settings lê o .env) e todo teste daqui viraria 404.
    monkeypatch.setenv("META_WEBHOOK_PATH_SECRET", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _sign(body: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _text_payload(text: str = "oi", frm: str = "5511987654321") -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "contacts": [{"profile": {"name": "Andre"}}],
                            "messages": [
                                {
                                    "from": frm,
                                    "id": "wamid.TEST",
                                    "timestamp": "1",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


@pytest.fixture
def client():
    """App com store em memória e sender mock (não envia nada de verdade)."""
    store = InMemorySessionStore()
    sender = MockMessageSender()

    class _FakeResult:
        def scalar_one_or_none(self):
            return None

        def scalars(self):
            return self

        def first(self):
            return None

    class _FakeSession:
        async def execute(self, *_a, **_k):
            return _FakeResult()

        async def commit(self):
            pass

        async def rollback(self):
            pass

    @asynccontextmanager
    async def _fake_session():
        yield _FakeSession()

    app.dependency_overrides[get_session_store] = lambda: store
    app.dependency_overrides[get_message_sender] = lambda: sender
    app.dependency_overrides[get_session_factory] = lambda: _fake_session
    with TestClient(app) as c:
        c.sender = sender  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Verificação da URL (o GET que a Meta faz ao cadastrar)                       #
# --------------------------------------------------------------------------- #
def test_verification_returns_the_challenge_as_plain_text(client):
    r = client.get(
        PATH,
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "1158201444",
        },
    )
    assert r.status_code == 200
    # TEXTO PURO: se devolver JSON (com aspas), a Meta recusa a URL.
    assert r.text == "1158201444"


def test_verification_rejects_wrong_token(client):
    r = client.get(
        PATH,
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "token-errado",
            "hub.challenge": "123",
        },
    )
    assert r.status_code == 403


def test_verification_rejects_when_token_not_configured(client, monkeypatch):
    """Fail-closed: sem token configurado, ninguém cadastra a URL."""
    monkeypatch.setenv("META_VERIFY_TOKEN", "")
    get_settings.cache_clear()
    r = client.get(
        PATH,
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "",
            "hub.challenge": "123",
        },
    )
    assert r.status_code == 403


# --------------------------------------------------------------------------- #
# Assinatura                                                                   #
# --------------------------------------------------------------------------- #
def test_signature_helper_accepts_valid_and_rejects_tampered():
    body = b'{"a":1}'
    assert verify_signature(APP_SECRET, body, _sign(body)) is True
    # Corpo alterado com a mesma assinatura -> recusa.
    assert verify_signature(APP_SECRET, b'{"a":2}', _sign(body)) is False
    # Sem header, sem segredo, ou prefixo errado -> recusa.
    assert verify_signature(APP_SECRET, body, None) is False
    assert verify_signature("", body, _sign(body)) is False
    assert verify_signature(APP_SECRET, body, "sha1=abc") is False


def test_post_without_signature_is_rejected(client):
    r = client.post(PATH, json=_text_payload())
    assert r.status_code == 401


def test_post_with_wrong_signature_is_rejected(client):
    raw = json.dumps(_text_payload()).encode()
    r = client.post(
        PATH,
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(raw, "outro-segredo"),
        },
    )
    assert r.status_code == 401


def test_post_with_valid_signature_is_processed(client):
    raw = json.dumps(_text_payload("oi")).encode()
    r = client.post(
        PATH,
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(raw),
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["replies"] >= 1
    # Respondeu para o número canônico (sem o 55).
    assert client.sender.sent[0][0] == "11987654321"


def test_status_events_are_ignored(client):
    """Entregue/lido não é mensagem: 200 sem responder nada ao cliente."""
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {"statuses": [{"status": "delivered"}]},
                    }
                ]
            }
        ],
    }
    raw = json.dumps(payload).encode()
    r = client.post(
        PATH,
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(raw),
        },
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"
    assert client.sender.sent == []


# --------------------------------------------------------------------------- #
# Extração de payload e telefone                                               #
# --------------------------------------------------------------------------- #
def test_button_reply_becomes_the_button_title():
    """Na API oficial os botões funcionam; a resposta chega como interactive."""
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "5511987654321",
                                    "id": "w",
                                    "type": "interactive",
                                    "interactive": {
                                        "type": "button_reply",
                                        "button_reply": {
                                            "id": "2",
                                            "title": "Resgatar pontos",
                                        },
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    msg = MetaWebhook(**payload).extract_text_message()
    assert msg is not None
    assert msg.text == "Resgatar pontos"


def test_media_message_is_ignored():
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "5511987654321",
                                    "id": "w",
                                    "type": "image",
                                    "image": {"id": "123"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    assert MetaWebhook(**payload).extract_text_message() is None


def test_phone_translation_roundtrip():
    assert meta_to_canonical("5511987654321") == "11987654321"
    assert canonical_to_meta("11987654321") == "5511987654321"
    # Idempotente nos dois sentidos.
    assert canonical_to_meta("5511987654321") == "5511987654321"
    assert meta_to_canonical("11987654321") == "11987654321"


# --------------------------------------------------------------------------- #
# Regressões encontradas na revisão de código                                  #
# --------------------------------------------------------------------------- #
def test_signature_with_non_ascii_header_is_rejected_not_crashed():
    """Header forjado com byte não-ASCII deve RECUSAR, não estourar.

    Cabeçalhos HTTP decodificam como latin-1, e `compare_digest` sobre duas
    str levanta TypeError com caractere não-ASCII. Comparando em str, uma
    assinatura forjada com um byte alto virava HTTP 500 em vez de 401 — um
    atacante derrubava a requisição de propósito.
    """
    forjada = "sha256=" + chr(0xE9) * 64
    assert verify_signature(APP_SECRET, b"{}", forjada) is False


def test_forged_non_ascii_signature_returns_401(client):
    """Mesmo ataque, agora atravessando o HTTP de verdade.

    O header vai como BYTES porque é assim que ele chega pela rede — o cliente
    httpx recusaria uma str não-ASCII, mas o atacante não usa httpx. O Starlette
    decodifica esses bytes como latin-1, que era exatamente o caminho até o
    TypeError.
    """
    raw = json.dumps(_text_payload()).encode()
    r = client.post(
        PATH,
        content=raw,
        headers={
            "Content-Type": "application/json",
            # 0xE9 = 'é' em latin-1: byte válido no header, inválido em ASCII.
            "X-Hub-Signature-256": b"sha256=" + bytes([0xE9]) * 64,
        },
    )
    assert r.status_code == 401


def test_batched_payload_answers_every_message(client):
    """A Meta agrupa eventos: um POST pode trazer várias mensagens.

    Atender só a primeira e responder 200 descarta as demais — a Meta considera
    o lote entregue e nunca reenvia. O cliente fica sem resposta, sem erro.
    """
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "messages": [
                                {
                                    "from": "5511900000001",
                                    "id": "w1",
                                    "type": "text",
                                    "text": {"body": "oi"},
                                },
                                {
                                    "from": "5511900000002",
                                    "id": "w2",
                                    "type": "text",
                                    "text": {"body": "oi"},
                                },
                            ],
                        },
                    }
                ],
            }
        ],
    }
    raw = json.dumps(payload).encode()
    r = client.post(
        PATH,
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(raw),
        },
    )
    assert r.status_code == 200
    assert r.json()["messages"] == 2

    # Os DOIS clientes foram respondidos.
    destinos = {destino for destino, _ in client.sender.sent}
    assert destinos == {"11900000001", "11900000002"}


def test_meta_sender_requires_token_and_phone_id(monkeypatch):
    """Config pela metade tem que quebrar na subida, não silenciosamente.

    Sem token/phone_number_id a URL sai como '.../v21.0//messages' e todo envio
    falha; como o webhook engole exceção de envio, a resposta seria
    {"status":"ok","sent":0} — saudável na aparência, mudo na prática.
    """
    from app.dependencies import get_message_sender

    monkeypatch.setenv("USE_MOCK_WHATSAPP", "false")
    monkeypatch.setenv("USE_META_WHATSAPP", "true")
    monkeypatch.setenv("META_ACCESS_TOKEN", "")
    monkeypatch.setenv("META_PHONE_NUMBER_ID", "")
    get_settings.cache_clear()
    get_message_sender.cache_clear()

    with pytest.raises(RuntimeError, match="META_ACCESS_TOKEN"):
        get_message_sender()

    get_message_sender.cache_clear()
    get_settings.cache_clear()
