"""Proteção do webhook da Meta por segredo no CAMINHO da URL.

Existe porque o App Secret pode não estar acessível. A Meta só chama uma URL e
não manda header customizado, então o caminho é o único segredo que dá para
exigir dela — a mesma proteção do webhook da Evolution que já roda hoje.

O que estes testes travam:

- com segredo de caminho, o caminho nu deixa de existir (404);
- errar o segredo é 404, não 403: não confirma que o endpoint existe;
- com App Secret configurado, o HMAC volta a valer SOZINHO ou JUNTO — os dois
  somam, nunca se substituem;
- sem NENHUM dos dois, recusa tudo (fail-closed).

O último é o mais importante: sem ele, esquecer as duas configurações deixaria
o endpoint aberto, e quem descobrisse a URL forjaria mensagens resgatando
cupons e atribuindo comissão em nome de clientes reais.
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
from app.whatsapp.session_store import InMemorySessionStore

VERIFY_TOKEN = "token-de-verificacao-de-teste"
APP_SECRET = "segredo-do-app-de-teste"
PATH_SECRET = "caminho-secreto-de-teste"
BASE = "/webhook/meta"
CORPO = {"object": "whatsapp_business_account", "entry": []}


def _configura(monkeypatch, *, app_secret: str, path_secret: str) -> None:
    monkeypatch.setenv("META_VERIFY_TOKEN", VERIFY_TOKEN)
    monkeypatch.setenv("META_APP_SECRET", app_secret)
    monkeypatch.setenv("META_WEBHOOK_PATH_SECRET", path_secret)
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _limpa_cache():
    yield
    get_settings.cache_clear()


@pytest.fixture
def client():
    """TestClient com as dependências de conversa neutralizadas.

    Estes testes param na FRONTEIRA (autorização); nenhum chega a tocar banco.
    Os overrides existem para que um payload que passe pela guarda não tente
    abrir conexão de verdade.
    """

    @asynccontextmanager
    async def _sem_banco():
        raise AssertionError("nenhum teste daqui deveria chegar ao banco")
        yield  # pragma: no cover

    app.dependency_overrides[get_message_sender] = lambda: MockMessageSender()
    app.dependency_overrides[get_session_store] = lambda: InMemorySessionStore()
    app.dependency_overrides[get_session_factory] = lambda: _sem_banco
    yield TestClient(app)
    app.dependency_overrides.clear()


def _handshake(client, path: str):
    return client.get(
        path,
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "1158201444",
        },
    )


def _assina(body: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# --------------------------------------------------------------------------- #
# Só o segredo de caminho (App Secret indisponível)                            #
# --------------------------------------------------------------------------- #


def test_handshake_no_caminho_secreto(client, monkeypatch):
    _configura(monkeypatch, app_secret="", path_secret=PATH_SECRET)

    r = _handshake(client, f"{BASE}/{PATH_SECRET}")

    assert r.status_code == 200
    assert r.text == "1158201444"
    assert r.headers["content-type"].startswith("text/plain")


def test_caminho_nu_deixa_de_existir(client, monkeypatch):
    """Com segredo configurado, /webhook/meta some — senão a ponte não protege."""
    _configura(monkeypatch, app_secret="", path_secret=PATH_SECRET)

    assert _handshake(client, BASE).status_code == 404
    assert client.post(BASE, json=CORPO).status_code == 404


def test_segredo_errado_responde_404(client, monkeypatch):
    """404, não 403: para quem erra o caminho, o endpoint não existe."""
    _configura(monkeypatch, app_secret="", path_secret=PATH_SECRET)

    assert _handshake(client, f"{BASE}/chute-errado").status_code == 404


def test_post_sem_assinatura_passa_no_caminho_secreto(client, monkeypatch):
    """Sem App Secret não há o que verificar; o caminho é a guarda."""
    _configura(monkeypatch, app_secret="", path_secret=PATH_SECRET)

    r = client.post(f"{BASE}/{PATH_SECRET}", json=CORPO)

    assert r.status_code == 200
    assert r.json() == {"status": "ignored"}


# --------------------------------------------------------------------------- #
# Degrau automático: App Secret entra e o HMAC passa a valer junto             #
# --------------------------------------------------------------------------- #


def test_app_secret_reativa_o_hmac_no_caminho_secreto(client, monkeypatch):
    """Os dois somam: acertar o caminho não dispensa a assinatura.

    É o que garante que colar o META_APP_SECRET no `.env` restaure a proteção
    forte sozinho, sem editar código nem depender de alguém lembrar de remover
    a ponte.
    """
    _configura(monkeypatch, app_secret=APP_SECRET, path_secret=PATH_SECRET)
    body = json.dumps(CORPO).encode()

    sem_assinatura = client.post(
        f"{BASE}/{PATH_SECRET}", content=body, headers={"Content-Type": "application/json"}
    )
    assert sem_assinatura.status_code == 401

    com_assinatura = client.post(
        f"{BASE}/{PATH_SECRET}",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _assina(body),
        },
    )
    assert com_assinatura.status_code == 200


def test_assinatura_valida_nao_salva_caminho_errado(client, monkeypatch):
    """A assinatura certa não compra passagem por um caminho errado."""
    _configura(monkeypatch, app_secret=APP_SECRET, path_secret=PATH_SECRET)
    body = json.dumps(CORPO).encode()

    r = client.post(
        f"{BASE}/chute-errado",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _assina(body),
        },
    )
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# Fail-closed                                                                  #
# --------------------------------------------------------------------------- #


def test_sem_nenhuma_protecao_recusa_tudo(client, monkeypatch):
    """Nenhum segredo configurado -> 403 em tudo, nunca endpoint aberto."""
    _configura(monkeypatch, app_secret="", path_secret="")

    assert _handshake(client, BASE).status_code == 403
    assert client.post(BASE, json=CORPO).status_code == 403
