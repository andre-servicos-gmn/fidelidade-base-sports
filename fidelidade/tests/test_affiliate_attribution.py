"""Testes de atribuição de afiliados (serviço + endpoint de stats).

Integração: banco real. Cobrem idempotência de `record_attribution`, agregação
de `get_affiliate_stats` e o endpoint `GET /admin/affiliates/stats`.
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

from app.auth.password import hash_password
from app.config import get_settings
from app.db.models import (
    AdminUser,
    Affiliate,
    AffiliateAttribution,
    AffiliateQuestion,
    AffiliateType,
    Customer,
    LedgerEntry,
    LedgerEntryType,
)
from app.dependencies import get_session_factory
from app.domain.security import hash_cpf
from app.main import app
from app.services.affiliate_service import (
    get_affiliate_stats,
    record_attribution,
)
from app.services.customer_service import get_or_create_customer
from app.services.ledger_service import add_entry, is_first_purchase

pytestmark = pytest.mark.integration

ADMIN_USER = "test-attr-admin"
ADMIN_PASS = "s3nh4-forte-teste"
CODE_PREFIX = "ATTRTEST-"
SRC_PREFIX = "ATTRTEST-tx-"
TEST_DOCS = ["86000000001", "86000000002"]


async def _purge(session: AsyncSession) -> None:
    # Atribuições primeiro (FK para customer/affiliate).
    await session.execute(
        delete(AffiliateAttribution).where(
            AffiliateAttribution.source_reference.like(f"{SRC_PREFIX}%")
        )
    )
    await session.execute(
        delete(Affiliate).where(Affiliate.code.like(f"{CODE_PREFIX}%"))
    )
    await session.execute(
        delete(AdminUser).where(AdminUser.username == ADMIN_USER)
    )
    hashes = [hash_cpf(d) for d in TEST_DOCS]
    # Lançamentos referenciam customer (FK): apagar ANTES dos clientes. Os
    # testes da trava de comissão criam compras no ledger.
    ids = [
        row[0]
        for row in (
            await session.execute(
                select(Customer.id).where(Customer.cpf_hash.in_(hashes))
            )
        ).all()
    ]
    if ids:
        # Perguntas de indicação também referenciam customer (FK).
        await session.execute(
            delete(AffiliateQuestion).where(AffiliateQuestion.customer_id.in_(ids))
        )
        await session.execute(
            delete(LedgerEntry).where(LedgerEntry.customer_id.in_(ids))
        )
    await session.execute(
        delete(Customer).where(Customer.cpf_hash.in_(hashes))
    )
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


async def _seed_affiliate(session: AsyncSession, code: str) -> Affiliate:
    affiliate = Affiliate(
        id=uuid.uuid4(),
        name=f"Afiliado {code}",
        affiliate_type=AffiliateType.INFLUENCER,
        code=code,
        active=True,
    )
    session.add(affiliate)
    await session.commit()
    return affiliate


# --------------------------------------------------------------------------- #
async def test_record_attribution_is_idempotent(session: AsyncSession):
    affiliate = await _seed_affiliate(session, f"{CODE_PREFIX}001")
    customer = await get_or_create_customer(session, TEST_DOCS[0])
    await session.commit()

    src = f"{SRC_PREFIX}1"
    first = await record_attribution(
        session,
        affiliate_id=affiliate.id,
        customer_id=customer.id,
        source_reference=src,
        points=100,
    )
    await session.commit()
    assert first is not None

    # Mesma compra de novo -> None (idempotente), sem segunda linha.
    second = await record_attribution(
        session,
        affiliate_id=affiliate.id,
        customer_id=customer.id,
        source_reference=src,
        points=100,
    )
    await session.commit()
    assert second is None

    rows = (
        await session.execute(
            select(AffiliateAttribution.id).where(
                AffiliateAttribution.source_reference == src
            )
        )
    ).all()
    assert len(rows) == 1


async def test_get_affiliate_stats_aggregates(session: AsyncSession):
    affiliate = await _seed_affiliate(session, f"{CODE_PREFIX}002")
    cust_a = await get_or_create_customer(session, TEST_DOCS[0])
    cust_b = await get_or_create_customer(session, TEST_DOCS[1])
    await session.commit()

    # Cliente A: 2 compras; Cliente B: 1 compra. (pontos do cliente, valor R$, pontos do afiliado)
    rows = [
        (cust_a, 100, Decimal("100.00"), 50),
        (cust_a, 50, Decimal("50.00"), 25),
        (cust_b, 30, Decimal("30.00"), 15),
    ]
    for i, (cust, pts, amount, aff_pts) in enumerate(rows):
        await record_attribution(
            session,
            affiliate_id=affiliate.id,
            customer_id=cust.id,
            source_reference=f"{SRC_PREFIX}agg{i}",
            points=pts,
            amount=amount,
            affiliate_points=aff_pts,
        )
    await session.commit()

    stats = await get_affiliate_stats(session)
    row = stats[affiliate.id]
    assert row.customers == 2
    assert row.purchases == 3
    assert row.points == 180
    assert row.affiliate_points == 90  # 50 + 25 + 15


async def test_stats_endpoint(session: AsyncSession):
    affiliate = await _seed_affiliate(session, f"{CODE_PREFIX}003")
    customer = await get_or_create_customer(session, TEST_DOCS[0])
    await session.commit()
    await record_attribution(
        session,
        affiliate_id=affiliate.id,
        customer_id=customer.id,
        source_reference=f"{SRC_PREFIX}ep1",
        points=70,
        amount=Decimal("70.00"),
        affiliate_points=35,
    )
    await session.commit()
    affiliate_id = str(affiliate.id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        login = await client.post(
            "/admin/login",
            json={"username": ADMIN_USER, "password": ADMIN_PASS},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        # Sem token -> 401.
        unauth = await client.get("/admin/affiliates/stats")
        assert unauth.status_code == 401

        resp = await client.get("/admin/affiliates/stats", headers=headers)
        assert resp.status_code == 200
        row = next(
            r for r in resp.json() if r["affiliate_id"] == affiliate_id
        )
        assert row["customers"] == 1
        assert row["purchases"] == 1
        assert row["points"] == 70
        assert row["affiliate_points"] == 35


# --------------------------------------------------------------------------- #
# Trava de comissão: só a PRIMEIRA compra do CPF remunera o afiliado           #
# --------------------------------------------------------------------------- #
async def test_is_first_purchase_identifies_only_the_first_earn(
    session: AsyncSession,
):
    """A trava depende disto: reconhecer qual compra foi a primeira do CPF.

    Regra: conta só lançamento EARN COM `source_reference` (veio da TouchPay).
    Resgates e ajustes ficam de fora, mesmo estando no mesmo ledger.
    """
    customer = await get_or_create_customer(session, TEST_DOCS[0])
    await session.commit()

    primeira = f"{SRC_PREFIX}first"
    segunda = f"{SRC_PREFIX}second"

    await add_entry(
        session,
        customer_id=customer.id,
        entry_type=LedgerEntryType.EARN,
        points=100,
        source_reference=primeira,
        description="1a compra",
    )
    await session.commit()

    assert await is_first_purchase(session, customer.id, primeira) is True

    # Um resgate no meio NÃO pode deslocar qual foi a primeira compra.
    await add_entry(
        session,
        customer_id=customer.id,
        entry_type=LedgerEntryType.REDEEM,
        points=-50,
        source_reference=None,
        description="resgate",
    )
    await add_entry(
        session,
        customer_id=customer.id,
        entry_type=LedgerEntryType.EARN,
        points=200,
        source_reference=segunda,
        description="2a compra",
    )
    await session.commit()

    assert await is_first_purchase(session, customer.id, primeira) is True
    assert await is_first_purchase(session, customer.id, segunda) is False


async def test_second_purchase_is_attributed_with_zero_commission(
    session: AsyncSession,
):
    """A 2ª compra ainda registra QUEM indicou, mas com comissão zero.

    A informação de indicação continua tendo valor para análise; o que a trava
    corta é o dinheiro, não o dado.
    """
    affiliate = await _seed_affiliate(session, f"{CODE_PREFIX}LOCK")
    customer = await get_or_create_customer(session, TEST_DOCS[1])
    await session.commit()

    segunda = f"{SRC_PREFIX}lock2"
    for src, pts in ((f"{SRC_PREFIX}lock1", 100), (segunda, 300)):
        await add_entry(
            session,
            customer_id=customer.id,
            entry_type=LedgerEntryType.EARN,
            points=pts,
            source_reference=src,
            description="compra",
        )
    await session.commit()

    assert await is_first_purchase(session, customer.id, segunda) is False

    # Como não é a primeira, a comissão gravada é zero.
    await record_attribution(
        session,
        affiliate_id=affiliate.id,
        customer_id=customer.id,
        source_reference=segunda,
        points=300,
        amount=Decimal("300.00"),
        affiliate_points=0,
    )
    await session.commit()

    row = (
        await session.execute(
            select(AffiliateAttribution).where(
                AffiliateAttribution.source_reference == segunda
            )
        )
    ).scalar_one()
    assert row.affiliate_points == 0
    assert row.points == 300  # os pontos do CLIENTE não são afetados
