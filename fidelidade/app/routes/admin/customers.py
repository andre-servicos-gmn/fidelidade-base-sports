"""Endpoints de consulta de cliente para SUPORTE (/admin/customers). Protegidos.

SEGURANÇA: as respostas NUNCA expõem o CPF completo nem o cpf_hash. Sempre
mascarado (123****89). Telefone também mascarado.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_admin
from app.db.models import Customer, LedgerEntry
from app.dependencies import get_db
from app.domain.security import hash_cpf, mask_cpf, mask_phone
from app.routes.admin.schemas import (
    CustomerCouponResponse,
    CustomerListResponse,
    CustomerResponse,
    LedgerEntryResponse,
)
from app.services.ledger_service import get_balance
from app.services.redemption_service import get_customer_coupons

router = APIRouter(
    prefix="/admin/customers",
    tags=["admin-customers"],
    dependencies=[Depends(get_current_admin)],
)


async def _get_or_404(session: AsyncSession, customer_id: uuid.UUID) -> Customer:
    customer = await session.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cliente não encontrado")
    return customer


@router.get("", response_model=CustomerResponse)
async def find_customer_by_cpf(
    cpf: str = Query(..., description="CPF do cliente (só dígitos ou formatado)"),
    session: AsyncSession = Depends(get_db),
) -> CustomerResponse:
    """Busca por CPF (aplica hash internamente). Resposta com CPF mascarado."""
    customer = (
        await session.execute(
            select(Customer).where(Customer.cpf_hash == hash_cpf(cpf))
        )
    ).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cliente não encontrado")

    balance = await get_balance(session, customer.id)
    return CustomerResponse(
        id=customer.id,
        cpf_masked=mask_cpf(cpf),
        phone_masked=mask_phone(customer.phone),
        balance=balance,
        created_at=customer.created_at,
    )


@router.get("/list", response_model=CustomerListResponse)
async def list_customers(
    page: int = Query(1, ge=1, description="Página, começando em 1"),
    page_size: int = Query(25, ge=1, le=200, description="Itens por página"),
    session: AsyncSession = Depends(get_db),
) -> CustomerListResponse:
    """Lista paginada de clientes, do cadastro mais recente para o mais antigo.

    POR QUE EXISTE: a busca por CPF é necessariamente EXATA — guardamos só o
    HMAC do CPF, e hash não preserva prefixo, então não há como procurar por
    "começa com 423". Sem esta listagem, um cliente cujo CPF completo não se
    sabe de cor fica invisível no painel (e parece que "não salvou").

    O saldo vem do `balance_after` do último lançamento de cada cliente, por
    subconsulta correlacionada — 0 para quem ainda não tem movimentação.

    Como no resto do módulo, CPF e telefone saem SEMPRE mascarados.
    """
    total = (
        await session.execute(select(func.count()).select_from(Customer))
    ).scalar_one()

    latest_balance = (
        select(LedgerEntry.balance_after)
        .where(LedgerEntry.customer_id == Customer.id)
        .order_by(LedgerEntry.sequence.desc())
        .limit(1)
        .correlate(Customer)
        .scalar_subquery()
    )

    rows = (
        await session.execute(
            select(Customer, func.coalesce(latest_balance, 0))
            .order_by(Customer.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    return CustomerListResponse(
        items=[
            CustomerResponse(
                id=customer.id,
                cpf_masked=customer.cpf_masked,
                phone_masked=mask_phone(customer.phone),
                balance=balance,
                created_at=customer.created_at,
            )
            for customer, balance in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{customer_id}/ledger", response_model=list[LedgerEntryResponse])
async def customer_ledger(
    customer_id: uuid.UUID, session: AsyncSession = Depends(get_db)
) -> list[LedgerEntry]:
    await _get_or_404(session, customer_id)
    entries = (
        await session.execute(
            select(LedgerEntry)
            .where(LedgerEntry.customer_id == customer_id)
            .order_by(LedgerEntry.sequence)
        )
    ).scalars().all()
    return list(entries)


@router.get("/{customer_id}/coupons", response_model=list[CustomerCouponResponse])
async def customer_coupons(
    customer_id: uuid.UUID, session: AsyncSession = Depends(get_db)
) -> list[dict]:
    await _get_or_404(session, customer_id)
    return await get_customer_coupons(session, customer_id)
