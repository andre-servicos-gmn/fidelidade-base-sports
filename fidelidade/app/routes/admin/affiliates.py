"""Endpoints de afiliados (/admin/affiliates). Todos protegidos por JWT.

Gestão de parceiros (professores/influencers) e seus códigos de divulgação. O
código é único; tentar criar/atualizar com um código já existente devolve 409.

Política de DELETE: hard delete (como em /admin/rules). Para um afiliado que já
divulgou o código, prefira DESATIVAR (PATCH /toggle) a excluir; o DELETE serve
para remover cadastros feitos por engano.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_admin
from app.db.models import Affiliate
from app.dependencies import get_db
from app.routes.admin.schemas import (
    AffiliateCreate,
    AffiliateResponse,
    AffiliateStatsResponse,
    AffiliateUpdate,
)
from app.services import affiliate_service
from app.services.affiliate_service import DuplicateAffiliateCodeError

router = APIRouter(
    prefix="/admin/affiliates",
    tags=["admin-affiliates"],
    dependencies=[Depends(get_current_admin)],
)

_CONFLICT = "Já existe um afiliado com este código."


async def _get_or_404(
    session: AsyncSession, affiliate_id: uuid.UUID
) -> Affiliate:
    affiliate = await affiliate_service.get_affiliate(session, affiliate_id)
    if affiliate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Afiliado não encontrado")
    return affiliate


@router.get("", response_model=list[AffiliateResponse])
async def list_affiliates(
    session: AsyncSession = Depends(get_db),
) -> list[Affiliate]:
    return await affiliate_service.list_affiliates(session)


@router.get("/stats", response_model=list[AffiliateStatsResponse])
async def affiliate_stats(
    session: AsyncSession = Depends(get_db),
) -> list[AffiliateStatsResponse]:
    """Métricas por afiliado: clientes, compras e pontos atribuídos."""
    stats = await affiliate_service.get_affiliate_stats(session)
    return [
        AffiliateStatsResponse(
            affiliate_id=affiliate_id,
            customers=s.customers,
            purchases=s.purchases,
            points=s.points,
            affiliate_points=s.affiliate_points,
        )
        for affiliate_id, s in stats.items()
    ]


@router.get("/{affiliate_id}", response_model=AffiliateResponse)
async def get_affiliate(
    affiliate_id: uuid.UUID, session: AsyncSession = Depends(get_db)
) -> Affiliate:
    return await _get_or_404(session, affiliate_id)


@router.post(
    "", response_model=AffiliateResponse, status_code=status.HTTP_201_CREATED
)
async def create_affiliate(
    body: AffiliateCreate, session: AsyncSession = Depends(get_db)
) -> Affiliate:
    try:
        affiliate = await affiliate_service.create_affiliate(
            session,
            name=body.name,
            affiliate_type=body.affiliate_type,
            code=body.code,
            points_rate=body.points_rate,
            contact=body.contact,
            notes=body.notes,
            active=body.active,
        )
    except DuplicateAffiliateCodeError:
        raise HTTPException(status.HTTP_409_CONFLICT, _CONFLICT)
    await session.commit()
    await session.refresh(affiliate)
    return affiliate


@router.put("/{affiliate_id}", response_model=AffiliateResponse)
async def update_affiliate(
    affiliate_id: uuid.UUID,
    body: AffiliateUpdate,
    session: AsyncSession = Depends(get_db),
) -> Affiliate:
    affiliate = await _get_or_404(session, affiliate_id)
    try:
        await affiliate_service.update_affiliate(
            session,
            affiliate,
            name=body.name,
            affiliate_type=body.affiliate_type,
            code=body.code,
            points_rate=body.points_rate,
            contact=body.contact,
            notes=body.notes,
            active=body.active,
        )
    except DuplicateAffiliateCodeError:
        raise HTTPException(status.HTTP_409_CONFLICT, _CONFLICT)
    await session.commit()
    await session.refresh(affiliate)
    return affiliate


@router.patch("/{affiliate_id}/toggle", response_model=AffiliateResponse)
async def toggle_affiliate(
    affiliate_id: uuid.UUID, session: AsyncSession = Depends(get_db)
) -> Affiliate:
    affiliate = await _get_or_404(session, affiliate_id)
    await affiliate_service.set_active(session, affiliate, not affiliate.active)
    await session.commit()
    await session.refresh(affiliate)
    return affiliate


@router.delete("/{affiliate_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_affiliate(
    affiliate_id: uuid.UUID, session: AsyncSession = Depends(get_db)
) -> None:
    affiliate = await _get_or_404(session, affiliate_id)
    await affiliate_service.delete_affiliate(session, affiliate)
    await session.commit()
