"""Testes da configuração persistente (regras no banco + seeds). Integração."""

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db.models import (
    CouponPool,
    Customer,
    LedgerEntry,
    ScoringRule,
)
from app.domain.scoring import RuleType
from app.domain.security import hash_cpf
from app.integrations.touchpay.mock_client import MockTouchPayClient
from app.services.ingestion_service import ingest_transactions
from app.services.rule_service import get_active_rules
from scripts.seed_config import (
    BASE_RULE_NAME,
    COUPONS_PER_TIER,
    REWARD_TIERS,
    TEST_PREFIX,
    seed_config,
)
from scripts.seed_test_customer import TEST_CPF, seed_test_customer

pytestmark = pytest.mark.integration

MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
MAX_DATE = datetime(2026, 6, 1, tzinfo=timezone.utc)

MOCK_DOCS = ["11111111111", "22222222222", "33333333333", "44444444444"]
TEST_RULE_NAME = "TESTRULE Base"
RULE_NAMES = [BASE_RULE_NAME, TEST_RULE_NAME]
ALL_DOCS = MOCK_DOCS + [TEST_CPF]
EXPECTED_TEST_COUPONS = len(REWARD_TIERS) * COUPONS_PER_TIER


async def _purge(session: AsyncSession) -> None:
    await session.execute(
        delete(CouponPool).where(CouponPool.code.like(f"{TEST_PREFIX}%"))
    )
    await session.execute(
        delete(ScoringRule).where(ScoringRule.name.in_(RULE_NAMES))
    )
    hashes = [hash_cpf(doc) for doc in ALL_DOCS]
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
async def session() -> AsyncIterator[AsyncSession]:
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL não configurada.")
    engine: AsyncEngine = create_async_engine(
        settings.database_url, poolclass=NullPool
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        await _purge(s)
        try:
            yield s
        finally:
            await _purge(s)
    await engine.dispose()


def _insert_base_rule(session: AsyncSession) -> None:
    session.add(
        ScoringRule(
            id=uuid.uuid4(),
            name=TEST_RULE_NAME,
            rule_type=RuleType.BASE,
            priority=100,
            active=True,
            params={"points_per_real": 1.0},
        )
    )


# --------------------------------------------------------------------------- #
async def test_get_active_rules_reads_and_converts(session: AsyncSession):
    _insert_base_rule(session)
    await session.commit()

    rules = await get_active_rules(session)

    mine = [r for r in rules if r.name == TEST_RULE_NAME]
    assert len(mine) == 1
    rule = mine[0]
    assert rule.rule_type is RuleType.BASE
    assert rule.params["points_per_real"] == 1.0
    assert rule.active is True


async def test_ingestion_uses_rules_from_db(session: AsyncSession):
    _insert_base_rule(session)
    await session.commit()

    # Sem passar `rules`: a ingestão carrega do banco.
    report = await ingest_transactions(
        session, MockTouchPayClient(), MIN_DATE, MAX_DATE
    )

    assert report.credited == 4
    assert report.total_points_credited == 680  # base 1/real


async def test_seed_config_is_idempotent(session: AsyncSession):
    first = await seed_config(session)
    assert first["base_rule"] == "criada"
    assert first["coupons_created"] == EXPECTED_TEST_COUPONS

    second = await seed_config(session)
    assert second["base_rule"] == "já existia"
    assert second["coupons_created"] == 0

    # Estado final estável: 1 regra base, N cupons de teste.
    rule_count = (
        await session.execute(
            select(func.count())
            .select_from(ScoringRule)
            .where(ScoringRule.name == BASE_RULE_NAME)
        )
    ).scalar_one()
    coupon_count = (
        await session.execute(
            select(func.count())
            .select_from(CouponPool)
            .where(CouponPool.code.like(f"{TEST_PREFIX}%"))
        )
    ).scalar_one()
    assert rule_count == 1
    assert coupon_count == EXPECTED_TEST_COUPONS


async def test_seed_test_customer_is_idempotent(session: AsyncSession):
    first = await seed_test_customer(session)
    assert first["credited_now"] is True
    assert first["balance"] == 1500

    second = await seed_test_customer(session)
    assert second["credited_now"] is False  # não credita de novo
    assert second["balance"] == 1500
