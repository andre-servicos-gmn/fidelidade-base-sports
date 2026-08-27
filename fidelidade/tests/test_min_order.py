"""Testes da propagação do 'pedido mínimo' (min_order_value).

- Mensagens do WhatsApp: puras (sem banco).
- Catálogo, seed e confirmação de resgate: @pytest.mark.integration.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
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
    ScoringRule,
)
from app.domain.redemption import RedemptionResult
from app.domain.security import hash_cpf
from app.services.customer_service import get_or_create_customer
from app.services.ledger_service import add_entry
from app.services.redemption_service import (
    list_available_rewards,
    make_reward_id,
    redeem_coupon,
)
from app.whatsapp import messages
from scripts.seed_config import BASE_RULE_NAME, TEST_PREFIX, seed_config


# --------------------------------------------------------------------------- #
# Mensagens (puras)                                                            #
# --------------------------------------------------------------------------- #
def _reward(min_order):
    return {
        "reward_id": "x",
        "discount_type": "FIXED",
        "discount_value": 10.0,
        "points_cost": 500,
        "available_count": 3,
        "min_order_value": min_order,
    }


def test_rewards_list_shows_condition_when_min_present():
    msg = messages.rewards_list([_reward(50.0)])
    assert "acima de R$50" in msg


def test_rewards_list_omits_condition_when_min_null():
    msg = messages.rewards_list([_reward(None)])
    assert "acima de" not in msg


def _result(min_order):
    return RedemptionResult(
        coupon_code="BASE-R10-0001",
        discount_type="FIXED",
        discount_value=Decimal("10.00"),
        points_spent=500,
        balance_after=100,
        expires_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        min_order_value=min_order,
    )


def test_redemption_success_repeats_condition():
    msg = messages.redemption_success(_result(Decimal("50.00")))
    assert "BASE-R10-0001" in msg
    assert "acima de R$50" in msg


def test_redemption_success_omits_condition_when_null():
    msg = messages.redemption_success(_result(None))
    assert "acima de" not in msg


def test_coupons_list_shows_condition():
    coupons = [
        {
            "code": "C-1",
            "discount_type": "FIXED",
            "discount_value": 25.0,
            "points_cost": 1000,
            "status": "ALLOCATED",
            "allocated_at": None,
            "expires_at": datetime(2026, 7, 1, tzinfo=timezone.utc),
            "min_order_value": 100.0,
        }
    ]
    msg = messages.coupons_list(coupons)
    assert "acima de R$100" in msg


# --------------------------------------------------------------------------- #
# Integração                                                                   #
# --------------------------------------------------------------------------- #
MIN_DOC = "00011122233"
MIN_PREFIX = "MINTEST-"


async def _purge(session: AsyncSession) -> None:
    await session.execute(
        delete(CouponPool).where(
            CouponPool.code.like(f"{MIN_PREFIX}%")
            | CouponPool.code.like(f"{TEST_PREFIX}%")
        )
    )
    await session.execute(
        delete(ScoringRule).where(ScoringRule.name == BASE_RULE_NAME)
    )
    ids = [
        r[0]
        for r in (
            await session.execute(
                select(Customer.id).where(Customer.cpf_hash == hash_cpf(MIN_DOC))
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


async def _add_coupon(session, code, value, cost, min_order):
    session.add(
        CouponPool(
            id=uuid.uuid4(),
            code=code,
            discount_type=CouponDiscountType.FIXED,
            discount_value=Decimal(str(value)),
            points_cost=cost,
            min_order_value=Decimal(str(min_order)) if min_order is not None else None,
            status=CouponStatus.AVAILABLE,
        )
    )
    await session.commit()


@pytest.mark.integration
async def test_catalog_returns_min_per_tier(session: AsyncSession):
    await _add_coupon(session, f"{MIN_PREFIX}R10", 10, 500, 50)
    await _add_coupon(session, f"{MIN_PREFIX}R25", 25, 1000, 100)

    rewards = await list_available_rewards(session)
    by_cost = {r["points_cost"]: r for r in rewards}
    assert by_cost[500]["min_order_value"] == 50.0
    assert by_cost[1000]["min_order_value"] == 100.0


@pytest.mark.integration
async def test_seed_creates_proportional_minimums(session: AsyncSession):
    await seed_config(session)

    async def min_for(tag: str) -> Decimal:
        row = (
            await session.execute(
                select(CouponPool.min_order_value).where(
                    CouponPool.code == f"{TEST_PREFIX}{tag}-0001"
                )
            )
        ).scalar_one()
        return row

    assert await min_for("R10") == Decimal("50.00")
    assert await min_for("R25") == Decimal("100.00")
    assert await min_for("R50") == Decimal("150.00")


@pytest.mark.integration
async def test_redemption_result_carries_min(session: AsyncSession):
    customer = await get_or_create_customer(session, MIN_DOC)
    await session.commit()
    customer_id = customer.id
    await add_entry(session, customer_id, LedgerEntryType.EARN, 600, "seed-min")
    await session.commit()
    await _add_coupon(session, f"{MIN_PREFIX}R10", 10, 500, 50)

    reward_id = make_reward_id(CouponDiscountType.FIXED, Decimal("10.00"), 500)
    result = await redeem_coupon(session, customer_id, reward_id)

    assert result.min_order_value == Decimal("50.00")
    # E a confirmação repete a condição.
    assert "acima de R$50" in messages.redemption_success(result)
