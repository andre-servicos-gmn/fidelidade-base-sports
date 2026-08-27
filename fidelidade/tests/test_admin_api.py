"""Testes da API administrativa (auth + rules + coupons + customers).

Integração: usam o banco real via ASGITransport. Cobrem autenticação, validação
de params, controle do motor pela API, resumo de cupons e mascaramento de CPF.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
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

from app.auth.password import hash_password
from app.config import get_settings
from app.db.models import (
    AdminUser,
    CouponDiscountType,
    CouponPool,
    CouponStatus,
    Customer,
    LedgerEntry,
    ScoringRule,
)
from app.dependencies import get_session_factory
from app.domain.scoring import RuleType
from app.domain.security import hash_cpf
from app.main import app
from app.services.customer_service import get_or_create_customer
from app.services.identity_service import link_phone_to_cpf
from app.services.ingestion_service import ingest_transactions
from app.integrations.touchpay.mock_client import MockTouchPayClient

pytestmark = pytest.mark.integration

ADMIN_USER = "test-admin"
ADMIN_PASS = "s3nh4-forte-teste"
TEST_CPF = "85000000001"
TEST_PHONE = "11955550001"
MOCK_DOCS = ["11111111111", "22222222222", "33333333333", "44444444444"]
RULE_NAMES = ["TEST Base", "TEST Raquetes 2x"]
COUPON_PREFIX = "ADMINTEST-"

MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
MAX_DATE = datetime(2026, 6, 1, tzinfo=timezone.utc)


async def _purge(session: AsyncSession) -> None:
    await session.execute(
        delete(CouponPool).where(CouponPool.code.like(f"{COUPON_PREFIX}%"))
    )
    await session.execute(
        delete(ScoringRule).where(ScoringRule.name.in_(RULE_NAMES))
    )
    await session.execute(
        delete(AdminUser).where(AdminUser.username == ADMIN_USER)
    )
    hashes = [hash_cpf(d) for d in (MOCK_DOCS + [TEST_CPF])]
    ids = [
        r[0]
        for r in (
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
        # cria o admin de teste
        s.add(
            AdminUser(
                id=uuid.uuid4(),
                username=ADMIN_USER,
                password_hash=hash_password(ADMIN_PASS),
                name="Teste",
                active=True,
            )
        )
        await s.commit()
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


@pytest_asyncio.fixture(autouse=True)
async def _wire_app(engine: AsyncEngine) -> AsyncIterator[None]:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    app.dependency_overrides[get_session_factory] = lambda: maker
    yield
    app.dependency_overrides.clear()


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _login(client: AsyncClient, user=ADMIN_USER, pwd=ADMIN_PASS):
    return await client.post("/admin/login", json={"username": user, "password": pwd})


async def _auth(client: AsyncClient) -> dict:
    resp = await _login(client)
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# --------------------------------------------------------------------------- #
# Auth                                                                         #
# --------------------------------------------------------------------------- #
async def test_login_success_and_failure():
    async with _client() as client:
        ok = await _login(client)
        assert ok.status_code == 200
        assert ok.json()["token_type"] == "bearer"
        assert ok.json()["access_token"]

        bad_pwd = await _login(client, pwd="errada")
        assert bad_pwd.status_code == 401

        bad_user = await _login(client, user="naoexiste")
        assert bad_user.status_code == 401


async def test_all_admin_groups_require_auth():
    async with _client() as client:
        for path in (
            "/admin/rules",
            "/admin/coupons",
            f"/admin/customers?cpf={TEST_CPF}",
        ):
            resp = await client.get(path)
            assert resp.status_code == 401, path


# --------------------------------------------------------------------------- #
# Regras                                                                       #
# --------------------------------------------------------------------------- #
async def test_create_rule_valid_and_invalid_params():
    async with _client() as client:
        headers = await _auth(client)

        ok = await client.post(
            "/admin/rules",
            headers=headers,
            json={
                "name": "TEST Base",
                "rule_type": "BASE",
                "priority": 100,
                "params": {"points_per_real": 1.0},
            },
        )
        assert ok.status_code == 201
        assert ok.json()["rule_type"] == "BASE"

        # CATEGORY_MULTIPLIER sem multiplier -> 422.
        bad = await client.post(
            "/admin/rules",
            headers=headers,
            json={
                "name": "TEST Raquetes 2x",
                "rule_type": "CATEGORY_MULTIPLIER",
                "params": {"category": "Raquetes"},
            },
        )
        assert bad.status_code == 422


async def test_rule_created_via_api_controls_engine(session: AsyncSession):
    async with _client() as client:
        headers = await _auth(client)
        await client.post(
            "/admin/rules",
            headers=headers,
            json={
                "name": "TEST Base",
                "rule_type": "BASE",
                "priority": 100,
                "params": {"points_per_real": 1.0},
            },
        )
        await client.post(
            "/admin/rules",
            headers=headers,
            json={
                "name": "TEST Raquetes 2x",
                "rule_type": "CATEGORY_MULTIPLIER",
                "priority": 10,
                "params": {"category": "Raquetes", "multiplier": 2.0},
            },
        )

    # Ingestão SEM passar regras -> carrega do banco (inclui a raquetes 2x).
    report = await ingest_transactions(
        session, MockTouchPayClient(), MIN_DATE, MAX_DATE
    )
    # Base-only daria 680; com raquetes 2x (produtos id=1) o total sobe p/ 1075.
    assert report.credited == 4
    assert report.total_points_credited == 1075


# --------------------------------------------------------------------------- #
# Cupons                                                                       #
# --------------------------------------------------------------------------- #
async def test_coupons_batch_and_summary():
    async with _client() as client:
        headers = await _auth(client)
        codes = [f"{COUPON_PREFIX}{i:03d}" for i in range(1, 6)]

        created = await client.post(
            "/admin/coupons",
            headers=headers,
            json={
                "codes": codes,
                "discount_type": "FIXED",
                "discount_value": "25.00",
                "points_cost": 1000,
            },
        )
        assert created.status_code == 201
        assert len(created.json()["created"]) == 5
        assert "TouchPay" in created.json()["warning"]

        listing = await client.get(
            "/admin/coupons?status=AVAILABLE", headers=headers
        )
        assert listing.status_code == 200
        data = listing.json()
        ours = [c for c in data["items"] if c["code"].startswith(COUPON_PREFIX)]
        assert len(ours) == 5
        # resumo agregado da faixa 25/1000
        row = next(
            r for r in data["summary"] if r["points_cost"] == 1000
            and r["discount_value"] == 25.0
        )
        assert row["available"] >= 5


async def test_delete_available_vs_allocated_coupon(session: AsyncSession):
    # Cria um AVAILABLE e um ALLOCATED direto no banco.
    available = CouponPool(
        id=uuid.uuid4(), code=f"{COUPON_PREFIX}AV", discount_type=CouponDiscountType.FIXED,
        discount_value=Decimal("10.00"), points_cost=500, status=CouponStatus.AVAILABLE,
    )
    allocated = CouponPool(
        id=uuid.uuid4(), code=f"{COUPON_PREFIX}AL", discount_type=CouponDiscountType.FIXED,
        discount_value=Decimal("10.00"), points_cost=500, status=CouponStatus.ALLOCATED,
    )
    session.add_all([available, allocated])
    await session.commit()
    av_id, al_id = str(available.id), str(allocated.id)

    async with _client() as client:
        headers = await _auth(client)
        ok = await client.delete(f"/admin/coupons/{av_id}", headers=headers)
        assert ok.status_code == 204

        bad = await client.delete(f"/admin/coupons/{al_id}", headers=headers)
        assert bad.status_code == 409


# --------------------------------------------------------------------------- #
# Clientes (mascaramento)                                                      #
# --------------------------------------------------------------------------- #
async def test_customer_lookup_masks_cpf(session: AsyncSession):
    customer = await get_or_create_customer(session, TEST_CPF)
    await session.commit()
    await link_phone_to_cpf(session, TEST_PHONE, TEST_CPF)

    async with _client() as client:
        headers = await _auth(client)
        resp = await client.get(
            f"/admin/customers?cpf={TEST_CPF}", headers=headers
        )

    assert resp.status_code == 200
    body = resp.json()
    raw = resp.text
    # CPF completo e hash NUNCA aparecem.
    assert TEST_CPF not in raw
    assert hash_cpf(TEST_CPF) not in raw
    assert body["cpf_masked"] == "850****01"
    assert body["phone_masked"].endswith("0001")
    assert body["phone_masked"] != TEST_PHONE


async def test_customer_ledger_requires_auth_and_returns_history(
    session: AsyncSession,
):
    customer = await get_or_create_customer(session, TEST_CPF)
    await session.commit()
    customer_id = str(customer.id)

    async with _client() as client:
        # sem token -> 401
        unauth = await client.get(f"/admin/customers/{customer_id}/ledger")
        assert unauth.status_code == 401

        headers = await _auth(client)
        ok = await client.get(
            f"/admin/customers/{customer_id}/ledger", headers=headers
        )
        assert ok.status_code == 200
        assert isinstance(ok.json(), list)
