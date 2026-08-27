"""Endpoints de regras de pontuação (/admin/rules). Todos protegidos.

Como a ingestão (Fase 3) lê `get_active_rules` do banco a cada rodada, uma
regra criada/alterada aqui entra em vigor AUTOMATICAMENTE na próxima ingestão.

Política de DELETE: fazemos delete REAL (hard). Como o ledger não registra qual
regra gerou cada lançamento, não há como detectar com segurança se uma regra já
foi "usada". Para regras em produção, prefira DESATIVAR (PATCH /toggle ou
active=False) em vez de deletar; o DELETE serve para remover regras criadas por
engano.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_admin
from app.db.models import ScoringRule
from app.dependencies import get_db
from app.routes.admin.schemas import RuleCreate, RuleResponse, RuleUpdate

router = APIRouter(
    prefix="/admin/rules",
    tags=["admin-rules"],
    dependencies=[Depends(get_current_admin)],
)


async def _get_or_404(session: AsyncSession, rule_id: uuid.UUID) -> ScoringRule:
    rule = await session.get(ScoringRule, rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Regra não encontrada")
    return rule


@router.get("", response_model=list[RuleResponse])
async def list_rules(session: AsyncSession = Depends(get_db)) -> list[ScoringRule]:
    rows = (
        await session.execute(
            select(ScoringRule).order_by(ScoringRule.priority, ScoringRule.name)
        )
    ).scalars().all()
    return list(rows)


@router.get("/{rule_id}", response_model=RuleResponse)
async def get_rule(
    rule_id: uuid.UUID, session: AsyncSession = Depends(get_db)
) -> ScoringRule:
    return await _get_or_404(session, rule_id)


@router.post("", response_model=RuleResponse, status_code=status.HTTP_201_CREATED)
async def create_rule(
    body: RuleCreate, session: AsyncSession = Depends(get_db)
) -> ScoringRule:
    rule = ScoringRule(
        id=uuid.uuid4(),
        name=body.name,
        rule_type=body.rule_type,
        priority=body.priority,
        active=body.active,
        valid_from=body.valid_from,
        valid_until=body.valid_until,
        params=body.params,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return rule


@router.put("/{rule_id}", response_model=RuleResponse)
async def update_rule(
    rule_id: uuid.UUID,
    body: RuleUpdate,
    session: AsyncSession = Depends(get_db),
) -> ScoringRule:
    rule = await _get_or_404(session, rule_id)
    rule.name = body.name
    rule.rule_type = body.rule_type
    rule.priority = body.priority
    rule.active = body.active
    rule.valid_from = body.valid_from
    rule.valid_until = body.valid_until
    rule.params = body.params
    await session.commit()
    await session.refresh(rule)
    return rule


@router.patch("/{rule_id}/toggle", response_model=RuleResponse)
async def toggle_rule(
    rule_id: uuid.UUID, session: AsyncSession = Depends(get_db)
) -> ScoringRule:
    rule = await _get_or_404(session, rule_id)
    rule.active = not rule.active
    await session.commit()
    await session.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: uuid.UUID, session: AsyncSession = Depends(get_db)
) -> None:
    rule = await _get_or_404(session, rule_id)
    await session.delete(rule)
    await session.commit()
