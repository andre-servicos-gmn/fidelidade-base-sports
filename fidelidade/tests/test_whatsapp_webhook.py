"""Testes do adapter da Evolution API.

- Tradução de telefone (puro, sem banco).
- Webhook: auth, ignorar não-texto/fromMe, casamento de número, end-to-end.
  Os que tocam o banco são @pytest.mark.integration.
"""

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db.models import (
    CouponDiscountType,
    CouponPool,
    CouponStatus,
    Customer,
    LedgerEntry,
    LedgerEntryType,
)
from app.dependencies import (
    get_message_sender,
    get_session_factory,
    get_session_store,
)
from app.domain.security import hash_cpf
from app.main import app
from app.services.customer_service import get_or_create_customer
from app.services.identity_service import (
    find_customer_by_phone,
    link_phone_to_cpf,
)
from app.services.ledger_service import add_entry, get_balance
from app.services.redemption_service import make_reward_id
from app.whatsapp.evolution.phone import (
    canonical_to_evolution,
    evolution_to_canonical,
)
from app.whatsapp.evolution.sender import MockMessageSender
from app.whatsapp.session_store import InMemorySessionStore

WEBHOOK_PATH = "/webhook/whatsapp"
HEADER = "X-Webhook-Token"


# --------------------------------------------------------------------------- #
# Tradução de telefone (puro)                                                  #
# --------------------------------------------------------------------------- #
def test_evolution_to_canonical_all_cases():
    # Com sufixo + 55.
    assert evolution_to_canonical("5511987654321@s.whatsapp.net") == "11987654321"
    # Sufixo @c.us.
    assert evolution_to_canonical("5511987654321@c.us") == "11987654321"
    # Sem sufixo, com 55.
    assert evolution_to_canonical("5511987654321") == "11987654321"
    # Já canônico (sem 55, sem sufixo) -> idempotente.
    assert evolution_to_canonical("11987654321") == "11987654321"
    # Sufixo de device :NN não vaza para o número.
    assert evolution_to_canonical("5511987654321:12@s.whatsapp.net") == "11987654321"
    # Fixo (8 dígitos + DDD) com 55.
    assert evolution_to_canonical("551133334444") == "1133334444"


def test_canonical_to_evolution_all_cases():
    assert canonical_to_evolution("11987654321") == "5511987654321"
    # Idempotente: aceita já com 55.
    assert canonical_to_evolution("5511987654321") == "5511987654321"
    assert canonical_to_evolution("1133334444") == "551133334444"


def test_round_trip():
    canonical = "11987654321"
    assert evolution_to_canonical(canonical_to_evolution(canonical)) == canonical


# --------------------------------------------------------------------------- #
# Infra de teste do webhook                                                    #
# --------------------------------------------------------------------------- #
WA_DOCS = ["81000000001", "81000000002"]
COUPON_PREFIX = "WAWEBHOOK-"
REWARD_VALUE = Decimal("10.00")
REWARD_COST = 500
REWARD_ID = make_reward_id(CouponDiscountType.FIXED, REWARD_VALUE, REWARD_COST)


def _msg_payload(remote_jid: str, text: str, from_me: bool = False) -> dict:
    return {
        "event": "messages.upsert",
        "instance": "test",
        "data": {
            "key": {"remoteJid": remote_jid, "fromMe": from_me, "id": "X1"},
            "pushName": "Cliente",
            "message": {"conversation": text},
            "messageType": "conversation",
        },
    }


def _image_payload(remote_jid: str) -> dict:
    return {
        "event": "messages.upsert",
        "instance": "test",
        "data": {
            "key": {"remoteJid": remote_jid, "fromMe": False, "id": "X2"},
            "message": {"imageMessage": {"url": "http://x/y.jpg"}},
            "messageType": "imageMessage",
        },
    }


def _token() -> str:
    return get_settings().webhook_token


def _override(sender=None, store=None, session_factory=None) -> None:
    if sender is not None:
        app.dependency_overrides[get_message_sender] = lambda: sender
    if store is not None:
        app.dependency_overrides[get_session_store] = lambda: store
    if session_factory is not None:
        app.dependency_overrides[get_session_factory] = lambda: session_factory


@pytest_asyncio.fixture(autouse=True)
async def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --------------------------------------------------------------------------- #
# Auth / ignorar (sem banco)                                                   #
# --------------------------------------------------------------------------- #
async def test_webhook_rejects_invalid_token():
    sender = MockMessageSender()
    _override(sender=sender, store=InMemorySessionStore(), session_factory=lambda: None)

    async with _client() as client:
        # Sem header.
        r1 = await client.post(WEBHOOK_PATH, json=_msg_payload("5511987654321@s.whatsapp.net", "oi"))
        # Header errado.
        r2 = await client.post(
            WEBHOOK_PATH,
            json=_msg_payload("5511987654321@s.whatsapp.net", "oi"),
            headers={HEADER: "errado"},
        )

    assert r1.status_code == 401
    assert r2.status_code == 401
    assert sender.sent == []  # nada processado


async def test_webhook_returns_200_when_sending_fails():
    """Falha de ENVIO não pode virar 500.

    Regressão de um erro real em campo: enviar para um número inexistente fez a
    Evolution responder 400, o `raise_for_status` propagou e o webhook devolveu
    500. Com 500, a Evolution REENTREGA o evento -> respostas duplicadas / loop
    de retry. O webhook deve responder 200 e apenas registrar a falha.
    """

    from contextlib import asynccontextmanager

    class _BoomSender(MockMessageSender):
        async def send_text(self, phone_canonical: str, text: str) -> None:
            raise RuntimeError("evolution 400: number does not exist")

    class _FakeResult:
        def scalar_one_or_none(self):
            return None  # telefone não vinculado a nenhum cliente

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

    store = InMemorySessionStore()
    _override(sender=_BoomSender(), store=store, session_factory=_fake_session)

    async with _client() as client:
        r = await client.post(
            WEBHOOK_PATH,
            json=_msg_payload("5511987654321@s.whatsapp.net", "oi"),
            headers={HEADER: _token()},
        )

    assert r.status_code == 200
    body = r.json()
    # Gerou a resposta (saudação + "quer participar?"), mas não conseguiu enviar.
    assert body["replies"] == 1
    assert body["sent"] == 0
    # O estado da conversa AVANÇOU mesmo com a falha de envio.
    state = await store.get("11987654321")
    assert state is not None


async def test_webhook_ignores_non_text_and_from_me():
    sender = MockMessageSender()
    _override(sender=sender, store=InMemorySessionStore(), session_factory=lambda: None)

    async with _client() as client:
        # Mídia (sem texto).
        r1 = await client.post(
            WEBHOOK_PATH,
            json=_image_payload("5511987654321@s.whatsapp.net"),
            headers={HEADER: _token()},
        )
        # Mensagem do próprio bot.
        r2 = await client.post(
            WEBHOOK_PATH,
            json=_msg_payload("5511987654321@s.whatsapp.net", "oi", from_me=True),
            headers={HEADER: _token()},
        )

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert sender.sent == []


# --------------------------------------------------------------------------- #
# Integração (banco real)                                                      #
# --------------------------------------------------------------------------- #
async def _purge(session: AsyncSession) -> None:
    await session.execute(
        delete(CouponPool).where(CouponPool.code.like(f"{COUPON_PREFIX}%"))
    )
    hashes = [hash_cpf(doc) for doc in WA_DOCS]
    ids = [
        row[0]
        for row in (
            await session.execute(
                select(Customer.id).where(Customer.cpf_hash.in_(hashes))
            )
        ).all()
    ]
    if ids:
        await session.execute(
            delete(LedgerEntry).where(LedgerEntry.customer_id.in_(ids))
        )
        await session.execute(delete(Customer).where(Customer.id.in_(ids)))
    await session.commit()


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL não configurada.")
    eng = create_async_engine(settings.database_url, poolclass=NullPool)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s:
        await _purge(s)
    try:
        yield eng
    finally:
        async with maker() as s:
            await _purge(s)
        await eng.dispose()


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s


async def _seed_customer(session, doc, phone_canonical, points=0):
    customer = await get_or_create_customer(session, doc)
    await session.commit()
    await link_phone_to_cpf(session, phone_canonical, doc)
    if points:
        await add_entry(session, customer.id, LedgerEntryType.EARN, points, f"seed-{doc}")
        await session.commit()
    return customer


async def _seed_coupons(session, n):
    for _ in range(n):
        session.add(
            CouponPool(
                id=uuid.uuid4(),
                code=f"{COUPON_PREFIX}{uuid.uuid4().hex[:8]}",
                discount_type=CouponDiscountType.FIXED,
                discount_value=REWARD_VALUE,
                points_cost=REWARD_COST,
                status=CouponStatus.AVAILABLE,
            )
        )
    await session.commit()


@pytest.mark.integration
async def test_webhook_number_with_55_matches_linked_customer(
    session: AsyncSession, engine: AsyncEngine
):
    """O número COM 55 da Evolution casa com o cliente vinculado SEM 55."""
    phone_canonical = "11987654321"
    await _seed_customer(session, WA_DOCS[0], phone_canonical, points=350)

    sender = MockMessageSender()
    maker = async_sessionmaker(engine, expire_on_commit=False)
    _override(sender=sender, store=InMemorySessionStore(), session_factory=maker)

    async with _client() as client:
        resp = await client.post(
            WEBHOOK_PATH,
            json=_msg_payload("5511987654321@s.whatsapp.net", "saldo"),
            headers={HEADER: _token()},
        )

    assert resp.status_code == 200
    # Respondeu para o número canônico, com o saldo correto.
    assert len(sender.sent) == 1
    sent_phone, sent_text = sender.sent[0]
    assert sent_phone == phone_canonical
    assert "350" in sent_text

    # NÃO criou um cadastro novo: continua existindo só um customer do CPF.
    count = len(
        (
            await session.execute(
                select(Customer.id).where(
                    Customer.cpf_hash == hash_cpf(WA_DOCS[0])
                )
            )
        ).all()
    )
    assert count == 1
    linked = await find_customer_by_phone(session, phone_canonical)
    assert linked is not None


@pytest.mark.integration
async def test_webhook_end_to_end_redeem_keeps_state(
    session: AsyncSession, engine: AsyncEngine
):
    """Dois POSTs (resgatar -> escolha) com o MESMO store: estado persiste."""
    phone_canonical = "11987654322"
    customer = await _seed_customer(
        session, WA_DOCS[1], phone_canonical, points=600
    )
    customer_id = customer.id
    await _seed_coupons(session, 1)

    sender = MockMessageSender()
    store = InMemorySessionStore()  # compartilhado entre os dois webhooks
    maker = async_sessionmaker(engine, expire_on_commit=False)
    _override(sender=sender, store=store, session_factory=maker)

    jid = "5511987654322@s.whatsapp.net"
    async with _client() as client:
        r1 = await client.post(
            WEBHOOK_PATH, json=_msg_payload(jid, "resgatar"),
            headers={HEADER: _token()},
        )
        r2 = await client.post(
            WEBHOOK_PATH, json=_msg_payload(jid, "1"),
            headers={HEADER: _token()},
        )

    assert r1.status_code == 200 and r2.status_code == 200
    # Primeiro balão: lista de recompensas. Último: código do cupom.
    assert any("pontos" in t for _, t in sender.sent)
    assert any(COUPON_PREFIX in t for _, t in sender.sent)

    # O resgate realmente debitou (estado conversacional persistiu).
    assert await get_balance(session, customer_id) == 100
