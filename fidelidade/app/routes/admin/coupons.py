"""Endpoints do pool de cupons (/admin/coupons). Todos protegidos.

CONTEXTO CRÍTICO: cadastrar um cupom aqui apenas REGISTRA o código no nosso
banco. O sistema NÃO cria o cupom no TouchPay. Os códigos devem corresponder a
cupons REAIS já gerados no painel da TouchPay/AMLabs — senão não funcionam no
totem. Essa observação vai na resposta do cadastro em lote.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_admin
from app.db.models import CouponPool, CouponStatus
from app.dependencies import get_db
from app.routes.admin.schemas import (
    CouponBatchCreate,
    CouponBatchResult,
    CouponListResponse,
    CouponResponse,
    RewardSummaryRow,
)

router = APIRouter(
    prefix="/admin/coupons",
    tags=["admin-coupons"],
    dependencies=[Depends(get_current_admin)],
)

_REAL_COUPON_WARNING = (
    "Estes códigos só funcionam no totem se corresponderem a cupons REAIS "
    "criados no painel da TouchPay/AMLabs. O sistema apenas registra o código "
    "aqui; ele NÃO cria o cupom no TouchPay."
)


@router.get("", response_model=CouponListResponse)
async def list_coupons(
    session: AsyncSession = Depends(get_db),
    status_filter: CouponStatus | None = Query(default=None, alias="status"),
    min_value: Decimal | None = Query(default=None),
    max_value: Decimal | None = Query(default=None),
) -> CouponListResponse:
    stmt = select(CouponPool)
    if status_filter is not None:
        stmt = stmt.where(CouponPool.status == status_filter)
    if min_value is not None:
        stmt = stmt.where(CouponPool.discount_value >= min_value)
    if max_value is not None:
        stmt = stmt.where(CouponPool.discount_value <= max_value)
    stmt = stmt.order_by(CouponPool.points_cost, CouponPool.code)

    coupons = (await session.execute(stmt)).scalars().all()

    # Resumo agregado por faixa (discount_value, points_cost) x status.
    summary: dict[tuple, RewardSummaryRow] = {}
    for c in coupons:
        key = (c.discount_type.value, float(c.discount_value), c.points_cost)
        row = summary.get(key)
        if row is None:
            row = RewardSummaryRow(
                discount_type=c.discount_type.value,
                discount_value=float(c.discount_value),
                points_cost=c.points_cost,
                min_order_value=(
                    float(c.min_order_value)
                    if c.min_order_value is not None
                    else None
                ),
            )
            summary[key] = row
        if c.status is CouponStatus.AVAILABLE:
            row.available += 1
        elif c.status is CouponStatus.ALLOCATED:
            row.allocated += 1
        elif c.status is CouponStatus.USED:
            row.used += 1
        elif c.status is CouponStatus.EXPIRED:
            row.expired += 1

    return CouponListResponse(
        items=[CouponResponse.model_validate(c) for c in coupons],
        summary=list(summary.values()),
    )


@router.post("", response_model=CouponBatchResult, status_code=status.HTTP_201_CREATED)
async def create_coupons(
    body: CouponBatchCreate, session: AsyncSession = Depends(get_db)
) -> CouponBatchResult:
    created: list[str] = []
    skipped: list[str] = []
    for code in body.codes:
        code = code.strip()
        if not code:
            continue
        exists = (
            await session.execute(
                select(CouponPool.id).where(CouponPool.code == code)
            )
        ).scalar_one_or_none()
        if exists is not None:
            skipped.append(code)
            continue
        session.add(
            CouponPool(
                id=uuid.uuid4(),
                code=code,
                discount_type=body.discount_type,
                discount_value=body.discount_value,
                points_cost=body.points_cost,
                min_order_value=body.min_order_value,
                status=CouponStatus.AVAILABLE,
                expires_at=body.expires_at,
            )
        )
        created.append(code)
    await session.commit()
    return CouponBatchResult(
        created=created, skipped=skipped, warning=_REAL_COUPON_WARNING
    )


@router.delete("/{coupon_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_coupon(
    coupon_id: uuid.UUID, session: AsyncSession = Depends(get_db)
) -> None:
    coupon = await session.get(CouponPool, coupon_id)
    if coupon is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cupom não encontrado")
    if coupon.status is not CouponStatus.AVAILABLE:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Não é possível remover um cupom com status {coupon.status.value}: "
            "ele já saiu do pool (alocado/usado/expirado).",
        )
    await session.delete(coupon)
    await session.commit()
