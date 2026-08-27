"""Testes do serviço de resgate (atomicidade + concorrência).

Tudo aqui toca o banco real -> @pytest.mark.integration.
A concorrência é testada de verdade: dois `redeem_coupon` rodando em paralelo
via `asyncio.gather`, cada um na SUA própria sessão/conexão.
"""

import asyncio
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
)
from app.domain.ledger import verify_chain
from app.domain.redemption import (
    InsufficientPointsError,
    NoCouponAvailableError,
    RedemptionResult,
)
from app.domain.security import hash_cpf
from app.services.customer_service import get_or_create_customer
from app.services.ledger_service import add_entry, get_balance
from app.services.redemption_service import (
    get_customer_coupons,
    list_available_rewards,
    make_reward_id,
    redeem_coupon,
)

pytestmark = pytest.mark.integration

# CPFs e códigos exclusivos deste arquivo de teste (para isolamento/limpeza).
REDEEM_DOCS = ["70000000001", "70000000002"]
COUPON_PREFIX = "TEST-"

REWARD_VALUE = Decimal("25.00")
REWARD_COST = 500
REWARD_ID = make_reward_id(CouponDiscountType.FIXED, REWARD_VALUE, REWARD_COST)


# --------------------------------------------------------------------------- #
# Infra                                                                        #
# --------------------------------------------------------------------------- #
async def _purge(session: AsyncSession) -> None:
    # Cupons primeiro (têm FK para customers).
    await session.execute(
        delete(CouponPool).where(CouponPool.code.like(f"{COUPON_PREFIX}%"))
    )
    hashes = [hash_cpf(doc) for doc in REDEEM_DOCS]
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


def _new_session(engine: AsyncEngine) -> AsyncSession:
    return async_sessionmaker(engine, expire_on_commit=False)()


# --- helpers de seed ------------------------------------------------------- #
async def _make_customer_with_points(
    session: AsyncSession, doc: str, points: int
) -> Customer:
    customer = await get_or_create_customer(session, doc)
    await session.commit()
    if points:
        await add_entry(
            session, customer.id, LedgerEntryType.EARN, points, f"seed-{doc}"
        )
        await session.commit()
    return customer


async def _make_coupons(
    session: AsyncSession,
    n: int,
    value: Decimal = REWARD_VALUE,
    cost: int = REWARD_COST,
) -> list[str]:
    codes = []
    for i in range(n):
        code = f"{COUPON_PREFIX}{uuid.uuid4().hex[:8]}"
        session.add(
            CouponPool(
                id=uuid.uuid4(),
                code=code,
                discount_type=CouponDiscountType.FIXED,
                discount_value=value,
                points_cost=cost,
                status=CouponStatus.AVAILABLE,
            )
        )
        codes.append(code)
    await session.commit()
    return codes


async def _count_allocated_for(session: AsyncSession, customer_id) -> int:
    rows = (
        await session.execute(
            select(CouponPool.id).where(
                CouponPool.allocated_to_customer_id == customer_id
            )
        )
    ).all()
    return len(rows)


# --------------------------------------------------------------------------- #
# Resgate feliz                                                                #
# --------------------------------------------------------------------------- #
async def test_happy_redemption(session: AsyncSession):
    customer = await _make_customer_with_points(session, REDEEM_DOCS[0], 600)
    codes = await _make_coupons(session, 1)

    result = await redeem_coupon(session, customer.id, REWARD_ID)

    assert isinstance(result, RedemptionResult)
    assert result.coupon_code == codes[0]
    assert result.points_spent == 500
    assert result.balance_after == 100
    assert await get_balance(session, customer.id) == 100

    coupon = (
        await session.execute(
            select(CouponPool).where(CouponPool.code == codes[0])
        )
    ).scalar_one()
    assert coupon.status == CouponStatus.ALLOCATED
    assert coupon.allocated_to_customer_id == customer.id
    assert coupon.allocated_at is not None

    # expires_at ~30 dias à frente.
    delta_days = (result.expires_at - datetime.now(timezone.utc)).days
    assert 29 <= delta_days <= 30


# --------------------------------------------------------------------------- #
# Saldo insuficiente -> efeito-zero                                            #
# --------------------------------------------------------------------------- #
async def test_insufficient_points_has_zero_effect(session: AsyncSession):
    customer = await _make_customer_with_points(session, REDEEM_DOCS[0], 100)
    # Captura o id ANTES do resgate: o rollback interno (efeito-zero) expira o
    # objeto ORM, e acessar customer.id depois dispararia um lazy-load síncrono.
    customer_id = customer.id
    codes = await _make_coupons(session, 1)

    with pytest.raises(InsufficientPointsError) as exc:
        await redeem_coupon(session, customer_id, REWARD_ID)
    assert exc.value.balance == 100
    assert exc.value.cost == 500

    # Efeito-zero: saldo intacto, cupom AVAILABLE, só o lançamento de seed.
    assert await get_balance(session, customer_id) == 100
    coupon = (
        await session.execute(
            select(CouponPool).where(CouponPool.code == codes[0])
        )
    ).scalar_one()
    assert coupon.status == CouponStatus.AVAILABLE
    assert coupon.allocated_to_customer_id is None

    entries = (
        await session.execute(
            select(LedgerEntry).where(LedgerEntry.customer_id == customer_id)
        )
    ).scalars().all()
    assert len(entries) == 1  # só o EARN de seed
    assert await _count_allocated_for(session, customer_id) == 0


# --------------------------------------------------------------------------- #
# Pool vazio -> efeito-zero                                                    #
# --------------------------------------------------------------------------- #
async def test_no_coupon_available(session: AsyncSession):
    customer = await _make_customer_with_points(session, REDEEM_DOCS[0], 600)
    customer_id = customer.id  # ver nota em test_insufficient_points_has_zero_effect
    # Nenhum cupom criado para essa recompensa.
    missing_reward = make_reward_id(
        CouponDiscountType.FIXED, Decimal("99.99"), 9999
    )

    with pytest.raises(NoCouponAvailableError):
        await redeem_coupon(session, customer_id, missing_reward)

    assert await get_balance(session, customer_id) == 600
    assert await _count_allocated_for(session, customer_id) == 0


# --------------------------------------------------------------------------- #
# Cadeia de hash após o débito                                                 #
# --------------------------------------------------------------------------- #
async def test_hash_chain_valid_after_redemption(session: AsyncSession):
    customer = await _make_customer_with_points(session, REDEEM_DOCS[0], 600)
    await _make_coupons(session, 1)

    await redeem_coupon(session, customer.id, REWARD_ID)

    entries = (
        await session.execute(
            select(LedgerEntry)
            .where(LedgerEntry.customer_id == customer.id)
            .order_by(LedgerEntry.sequence.asc())
        )
    ).scalars().all()
    assert len(entries) == 2  # EARN + REDEEM
    assert entries[-1].entry_type == LedgerEntryType.REDEEM
    assert entries[-1].balance_after == 100
    assert verify_chain(list(entries)) is True


# --------------------------------------------------------------------------- #
# Catálogo                                                                     #
# --------------------------------------------------------------------------- #
async def test_catalog_reflects_pool_and_decrements(session: AsyncSession):
    customer = await _make_customer_with_points(session, REDEEM_DOCS[0], 600)
    await _make_coupons(session, 2)

    before = {r["reward_id"]: r for r in await list_available_rewards(session)}
    assert REWARD_ID in before
    assert before[REWARD_ID]["available_count"] == 2
    assert before[REWARD_ID]["points_cost"] == 500
    assert before[REWARD_ID]["discount_value"] == 25.0

    await redeem_coupon(session, customer.id, REWARD_ID)

    after = {r["reward_id"]: r for r in await list_available_rewards(session)}
    assert after[REWARD_ID]["available_count"] == 1

    # "Meus cupons" mostra o resgatado.
    mine = await get_customer_coupons(session, customer.id)
    assert len(mine) == 1
    assert mine[0]["status"] == "ALLOCATED"


# --------------------------------------------------------------------------- #
# Concorrência                                                                 #
# --------------------------------------------------------------------------- #
async def test_concurrent_same_reward_distinct_coupons(
    session: AsyncSession, engine: AsyncEngine
):
    """Dois clientes resgatam a MESMA recompensa em paralelo -> cupons distintos."""
    c1 = await _make_customer_with_points(session, REDEEM_DOCS[0], 600)
    c2 = await _make_customer_with_points(session, REDEEM_DOCS[1], 600)
    await _make_coupons(session, 2)

    async def _redeem(customer_id) -> RedemptionResult:
        async with _new_session(engine) as s:
            return await redeem_coupon(s, customer_id, REWARD_ID)

    r1, r2 = await asyncio.gather(_redeem(c1.id), _redeem(c2.id))

    # Ambos resgataram, com cupons DIFERENTES (nunca o mesmo cupom 2x).
    assert isinstance(r1, RedemptionResult) and isinstance(r2, RedemptionResult)
    assert r1.coupon_code != r2.coupon_code


async def test_concurrent_single_coupon_one_winner(
    session: AsyncSession, engine: AsyncEngine
):
    """Dois clientes, UM cupom -> exatamente um vence; o outro: NoCouponAvailable."""
    c1 = await _make_customer_with_points(session, REDEEM_DOCS[0], 600)
    c2 = await _make_customer_with_points(session, REDEEM_DOCS[1], 600)
    await _make_coupons(session, 1)

    async def _redeem(customer_id):
        async with _new_session(engine) as s:
            return await redeem_coupon(s, customer_id, REWARD_ID)

    outcomes = await asyncio.gather(
        _redeem(c1.id), _redeem(c2.id), return_exceptions=True
    )

    successes = [o for o in outcomes if isinstance(o, RedemptionResult)]
    failures = [o for o in outcomes if isinstance(o, NoCouponAvailableError)]
    assert len(successes) == 1
    assert len(failures) == 1


async def test_concurrent_same_customer_cannot_overspend(
    session: AsyncSession, engine: AsyncEngine
):
    """Mesmo cliente, saldo p/ UM resgate, dois em paralelo -> não fica negativo."""
    customer = await _make_customer_with_points(session, REDEEM_DOCS[0], 500)
    await _make_coupons(session, 2)  # 2 cupons disponíveis, mas saldo só p/ 1

    async def _redeem():
        async with _new_session(engine) as s:
            return await redeem_coupon(s, customer.id, REWARD_ID)

    outcomes = await asyncio.gather(
        _redeem(), _redeem(), return_exceptions=True
    )

    successes = [o for o in outcomes if isinstance(o, RedemptionResult)]
    failures = [o for o in outcomes if isinstance(o, InsufficientPointsError)]
    assert len(successes) == 1
    assert len(failures) == 1

    # Saldo nunca negativo: exatamente um débito de 500 -> saldo 0.
    assert await get_balance(session, customer.id) == 0
    assert await _count_allocated_for(session, customer.id) == 1
