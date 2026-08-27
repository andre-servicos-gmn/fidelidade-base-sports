"""Testes do agendador de polling (`app.worker`).

Puros (sem banco): usamos uma `session_factory` fake e um `TouchPayClient`
controlado, injetados por monkeypatch. Cobrem:

- `run_once` chama `ingest_transactions` com a janela recente e dispara os
  prompts de afiliado no `session_store` (mesmo store da API).
- `run_forever` roda ao menos um ciclo, para no `stop_event` e não morre quando
  um ciclo levanta exceção.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timezone

import pytest

from app import worker
from app.services.ingestion_service import IngestionReport
from app.whatsapp.affiliate_prompt import AffiliatePrompt
from app.whatsapp.evolution.sender import MockMessageSender
from app.whatsapp.session_store import (
    ConversationStep,
    InMemorySessionStore,
)


@asynccontextmanager
async def _fake_session():
    """Context manager de sessão que não faz I/O (a sessão nem é usada:
    `ingest_transactions` é substituído)."""
    yield object()


def _fake_get_session_factory():
    """Espelha `get_session_factory()`: retorna um CALLABLE que, chamado, abre
    a sessão (context manager). `run_once` faz `get_session_factory()` e depois
    `session_factory()` — dois níveis."""
    return _fake_session


@pytest.fixture(autouse=True)
def _wire_singletons(monkeypatch):
    """Injeta um session_store e um sender de teste como os singletons da API.

    Assim `run_once` grava o estado no MESMO store que assertamos, provando o
    contrato "worker e webhook compartilham o store".
    """
    store = InMemorySessionStore()
    sender = MockMessageSender()
    monkeypatch.setattr(worker, "get_session_store", lambda: store)
    monkeypatch.setattr(worker, "get_message_sender", lambda: sender)
    monkeypatch.setattr(worker, "get_session_factory", _fake_get_session_factory)
    return store, sender


async def test_run_once_ingests_and_dispatches_prompts(_wire_singletons, monkeypatch):
    store, sender = _wire_singletons

    captured: dict = {}

    async def fake_ingest(session, client, min_date, max_date, **kwargs):
        captured["min_date"] = min_date
        captured["max_date"] = max_date
        return IngestionReport(
            total_read=1,
            credited=1,
            total_points_credited=350,
            affiliate_prompts=[
                AffiliatePrompt(
                    phone="11990000001",
                    points=350,
                    source_reference="tx-1",
                )
            ],
        )

    monkeypatch.setattr(worker, "ingest_transactions", fake_ingest)

    report = await worker.run_once(touchpay_client=object())

    assert report.credited == 1
    # A janela vai de (agora - janela) até agora, com max >= min.
    assert captured["max_date"] >= captured["min_date"]
    assert captured["max_date"].tzinfo is timezone.utc or captured[
        "max_date"
    ].tzinfo is not None

    # O prompt foi disparado: estado gravado no store + mensagem enviada.
    state = await store.get("11990000001")
    assert state is not None
    assert state.step is ConversationStep.AWAITING_AFFILIATE_CODE
    assert len(sender.sent) == 1
    assert sender.sent[0][0] == "11990000001"


async def test_run_once_no_prompts_sends_nothing(_wire_singletons, monkeypatch):
    store, sender = _wire_singletons

    async def fake_ingest(session, client, min_date, max_date, **kwargs):
        return IngestionReport(total_read=0, credited=0)

    monkeypatch.setattr(worker, "ingest_transactions", fake_ingest)

    report = await worker.run_once(touchpay_client=object())

    assert report.credited == 0
    assert sender.sent == []


async def test_run_forever_runs_a_cycle_then_stops(_wire_singletons, monkeypatch):
    calls = {"n": 0}

    async def fake_run_once(client):
        calls["n"] += 1
        return IngestionReport()

    monkeypatch.setattr(worker, "run_once", fake_run_once)
    monkeypatch.setattr(worker, "_build_touchpay_client", lambda: object())

    stop = asyncio.Event()

    async def _stopper():
        # Deixa ao menos um ciclo rodar, depois pede parada.
        while calls["n"] < 1:
            await asyncio.sleep(0)
        stop.set()

    await asyncio.gather(worker.run_forever(stop), _stopper())
    assert calls["n"] >= 1


async def test_run_forever_survives_a_failing_cycle(_wire_singletons, monkeypatch):
    calls = {"n": 0}

    async def flaky_run_once(client):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("falha simulada no 1o ciclo")
        return IngestionReport()

    monkeypatch.setattr(worker, "run_once", flaky_run_once)
    monkeypatch.setattr(worker, "_build_touchpay_client", lambda: object())
    # Intervalo minúsculo para o 2o ciclo vir logo após o 1o falhar.
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("POLL_INTERVAL_SECONDS", "0")
    get_settings.cache_clear()

    stop = asyncio.Event()

    async def _stopper():
        while calls["n"] < 2:
            await asyncio.sleep(0)
        stop.set()

    await asyncio.gather(worker.run_forever(stop), _stopper())
    # O worker NÃO morreu no 1o ciclo com erro: chegou ao 2o.
    assert calls["n"] >= 2
    get_settings.cache_clear()


def test_empty_poll_pdv_env_parses_as_none(monkeypatch):
    """Regressão: `POLL_POINT_OF_SALE_ID=` (vazio) no .env não pode quebrar o
    boot da app. Deve virar None, não erro de parsing de int."""
    from app.config import Settings

    monkeypatch.setenv("POLL_POINT_OF_SALE_ID", "")
    settings = Settings()
    assert settings.poll_point_of_sale_id is None

    monkeypatch.setenv("POLL_POINT_OF_SALE_ID", "42")
    settings = Settings()
    assert settings.poll_point_of_sale_id == 42
