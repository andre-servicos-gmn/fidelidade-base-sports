"""Serviço de resgate: troca de pontos por cupom, ATÔMICO e seguro sob concorrência.

Regra de ouro: debitar pontos e alocar o cupom é UMA operação única. Ou as duas
coisas acontecem (commit), ou nenhuma (rollback). Sob concorrência, nunca o
mesmo cupom é alocado duas vezes e nunca o saldo do cliente fica negativo.

----------------------------------------------------------------------------- #
DESENHO DA ASSINATURA: resgate por RECOMPENSA, não por id de cupom
----------------------------------------------------------------------------- #
O cliente conhece "a recompensa de R$25 que custa 500 pontos", não ids internos
de cupom. Por isso o resgate é por um `reward_id` opaco que identifica um TIPO
de recompensa = (discount_type, discount_value, points_cost). O catálogo
(`list_available_rewards`) devolve esse `reward_id`; o serviço escolhe sozinho
UM cupom AVAILABLE que corresponda. Use `make_reward_id(...)` para montá-lo.

----------------------------------------------------------------------------- #
CONCORRÊNCIA (escolhas documentadas)
----------------------------------------------------------------------------- #
1. Lock no cliente: `SELECT ... FOR UPDATE` na linha do cliente serializa as
   operações de saldo do MESMO cliente (mesma estratégia da Fase 3). Dois
   resgates simultâneos do mesmo cliente são processados em série: o segundo só
   lê o saldo depois que o primeiro debitou — impossível ficar negativo.
2. Seleção do cupom: `SELECT ... FOR UPDATE SKIP LOCKED`. Dois resgates
   concorrentes da MESMA recompensa pegam cupons DIFERENTES — o segundo pula
   (skip) a linha travada pelo primeiro em vez de esperar por ela. Assim nunca
   o mesmo cupom é alocado a dois clientes, e não há contenção desnecessária.

Política de negócio: os pontos são debitados no resgate. A validade do cupom
entregue é a CADASTRADA (espelha o cupom real da TouchPay, que é o que o totem
aplica); só sem validade cadastrada vale o prazo padrão de `expiration_days`.
Cupom já vencido nunca é entregue nem aparece no catálogo. Pontos NÃO retornam
automaticamente se o cupom expirar — mas a
porta fica aberta para isso no futuro (um job que varre cupons EXPIRED e credita
um lançamento de estorno; o ledger já suporta via entry_type ADJUST/EXPIRE).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    CouponDiscountType,
    CouponPool,
    CouponStatus,
    Customer,
    LedgerEntryType,
)
from app.domain.redemption import (
    CustomerNotFoundError,
    InsufficientPointsError,
    NoCouponAvailableError,
    RedemptionError,
    RedemptionResult,
)
from app.services.ledger_service import add_entry, get_balance

_REWARD_SEP = "|"


# --------------------------------------------------------------------------- #
# Identificador opaco de recompensa                                            #
# --------------------------------------------------------------------------- #
def make_reward_id(
    discount_type: CouponDiscountType,
    discount_value: Decimal,
    points_cost: int,
) -> str:
    """Monta o `reward_id` opaco a partir dos atributos da recompensa."""
    dt = discount_type.value if isinstance(discount_type, CouponDiscountType) else str(discount_type)
    return f"{dt}{_REWARD_SEP}{Decimal(discount_value)}{_REWARD_SEP}{int(points_cost)}"


def _parse_reward_id(reward_id: str) -> tuple[CouponDiscountType, Decimal, int]:
    """Decompõe um `reward_id` de volta em (discount_type, discount_value, points_cost)."""
    try:
        raw_type, raw_value, raw_cost = reward_id.split(_REWARD_SEP)
        return (
            CouponDiscountType(raw_type),
            Decimal(raw_value),
            int(raw_cost),
        )
    except (ValueError, KeyError) as exc:
        raise NoCouponAvailableError(reward_id) from exc


# --------------------------------------------------------------------------- #
# Validade                                                                     #
# --------------------------------------------------------------------------- #
def _not_expired(now: datetime):
    """Filtro SQL: cupom sem validade cadastrada, ou ainda dentro dela."""
    return or_(CouponPool.expires_at.is_(None), CouponPool.expires_at > now)


def coupon_expiry(
    registered: datetime | None, now: datetime, expiration_days: int
) -> datetime:
    """Validade informada ao cliente no resgate.

    A cadastrada vence: ela reflete o cupom real da TouchPay, e é essa data
    que o totem respeita. Antes, o resgate sobrescrevia tudo com hoje + 30
    dias — se o cupom real vencesse antes, o cliente recebia uma data falsa e
    só descobria no caixa, com os pontos já debitados.
    """
    if registered is not None:
        return registered
    return now + timedelta(days=expiration_days)


# --------------------------------------------------------------------------- #
# Resgate                                                                      #
# --------------------------------------------------------------------------- #
async def redeem_coupon(
    session: AsyncSession,
    customer_id: uuid.UUID,
    reward_id: str,
    expiration_days: int = 30,
) -> RedemptionResult:
    """Resgata um cupom da recompensa `reward_id` para o cliente. Atômico.

    Em caso de QUALQUER falha (saldo insuficiente, sem cupom, erro de banco),
    faz rollback total: nenhum ponto debitado, nenhum cupom alocado. Em sucesso,
    faz commit e retorna o `RedemptionResult` com o código do cupom.
    """
    discount_type, discount_value, points_cost = _parse_reward_id(reward_id)

    try:
        # 1) Trava a linha do cliente (serializa operações de saldo do mesmo
        #    cliente) e confirma que ele existe.
        locked_customer = (
            await session.execute(
                select(Customer.id)
                .where(Customer.id == customer_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if locked_customer is None:
            raise CustomerNotFoundError(customer_id)

        balance = await get_balance(session, customer_id)
        now = datetime.now(timezone.utc)

        # 2) Escolhe UM cupom AVAILABLE e não vencido da recompensa, com
        #    FOR UPDATE SKIP LOCKED.
        coupon = (
            await session.execute(
                select(CouponPool)
                .where(
                    CouponPool.status == CouponStatus.AVAILABLE,
                    CouponPool.discount_type == discount_type,
                    CouponPool.discount_value == discount_value,
                    CouponPool.points_cost == points_cost,
                    _not_expired(now),
                )
                .order_by(CouponPool.created_at, CouponPool.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if coupon is None:
            raise NoCouponAvailableError(reward_id)

        # 3) Valida saldo ANTES de qualquer efeito colateral.
        if coupon.points_cost > balance:
            raise InsufficientPointsError(balance, coupon.points_cost)

        # 4) Debita os pontos (REDEEM, negativo) — reusa o hash chaining.
        entry = await add_entry(
            session,
            customer_id=customer_id,
            entry_type=LedgerEntryType.REDEEM,
            points=-coupon.points_cost,
            source_reference=None,
            description=(
                f"Resgate cupom {coupon.code} (R${coupon.discount_value})"
            ),
        )
        if entry is None:  # não deve ocorrer (source_reference é None)
            raise RedemptionError("Falha ao registrar o débito do resgate.")

        # 5) Aloca o cupom ao cliente, com a validade certa (ver `coupon_expiry`).
        coupon.status = CouponStatus.ALLOCATED
        coupon.allocated_to_customer_id = customer_id
        coupon.allocated_at = now
        coupon.expires_at = coupon_expiry(coupon.expires_at, now, expiration_days)
        await session.flush()

        result = RedemptionResult(
            coupon_code=coupon.code,
            discount_type=coupon.discount_type.value,
            discount_value=coupon.discount_value,
            points_spent=coupon.points_cost,
            balance_after=entry.balance_after,
            expires_at=coupon.expires_at,
            min_order_value=coupon.min_order_value,
        )

        # 6) Commit: débito + alocação tornam-se duráveis juntos.
        await session.commit()
        return result
    except Exception:
        # Rollback total: efeito-zero garantido sob qualquer falha.
        await session.rollback()
        raise


# --------------------------------------------------------------------------- #
# Catálogo / consulta                                                          #
# --------------------------------------------------------------------------- #
async def list_available_rewards(session: AsyncSession) -> list[dict]:
    """Catálogo de recompensas com cupom AVAILABLE e não vencido (WhatsApp).

    Agrupa o pool por (discount_type, discount_value, points_cost) e devolve,
    para cada tipo com ao menos um cupom AVAILABLE, quantos restam e o
    `min_order_value` da faixa (assumido uniforme por faixa; usamos MAX como
    agregado representativo). `min_order_value` é None quando não há mínimo.
    """
    rows = (
        await session.execute(
            select(
                CouponPool.discount_type,
                CouponPool.discount_value,
                CouponPool.points_cost,
                func.count().label("available_count"),
                func.max(CouponPool.min_order_value).label("min_order_value"),
            )
            .where(
                CouponPool.status == CouponStatus.AVAILABLE,
                # Sem isto, o catálogo contava cupons vencidos como disponíveis:
                # o cliente escolhia a recompensa e levava "esgotou".
                _not_expired(datetime.now(timezone.utc)),
            )
            .group_by(
                CouponPool.discount_type,
                CouponPool.discount_value,
                CouponPool.points_cost,
            )
            .order_by(CouponPool.points_cost)
        )
    ).all()

    return [
        {
            "reward_id": make_reward_id(discount_type, discount_value, points_cost),
            "discount_type": discount_type.value,
            "discount_value": float(discount_value),
            "points_cost": points_cost,
            "available_count": available_count,
            "min_order_value": (
                float(min_order_value) if min_order_value is not None else None
            ),
        }
        for (
            discount_type,
            discount_value,
            points_cost,
            available_count,
            min_order_value,
        ) in rows
    ]


async def get_customer_coupons(
    session: AsyncSession, customer_id: uuid.UUID
) -> list[dict]:
    """Cupons já resgatados pelo cliente (ALLOCATED/USED/EXPIRED)."""
    coupons = (
        (
            await session.execute(
                select(CouponPool)
                .where(
                    CouponPool.allocated_to_customer_id == customer_id,
                    CouponPool.status.in_(
                        [
                            CouponStatus.ALLOCATED,
                            CouponStatus.USED,
                            CouponStatus.EXPIRED,
                        ]
                    ),
                )
                .order_by(CouponPool.allocated_at.desc())
            )
        )
        .scalars()
        .all()
    )

    return [
        {
            "code": c.code,
            "discount_type": c.discount_type.value,
            "discount_value": float(c.discount_value),
            "points_cost": c.points_cost,
            "status": c.status.value,
            "allocated_at": c.allocated_at,
            "expires_at": c.expires_at,
            "min_order_value": (
                float(c.min_order_value)
                if c.min_order_value is not None
                else None
            ),
        }
        for c in coupons
    ]
