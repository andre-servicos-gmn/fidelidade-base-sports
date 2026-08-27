"""Ponte banco -> motor de pontuação.

Lê as `ScoringRule` persistidas e as converte para os `Rule` do domínio
(Fase 2), prontos para `calculate_points`. Assim, mudanças de regra (futuro
painel) entram em vigor sem mexer em código.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ScoringRule
from app.domain.scoring import Rule


def _to_domain_rule(row: ScoringRule) -> Rule:
    """Converte uma linha ScoringRule no `Rule` do domínio via Rule.from_dict."""
    return Rule.from_dict(
        {
            "id": str(row.id),
            "name": row.name,
            "rule_type": row.rule_type.value,
            "priority": row.priority,
            "active": row.active,
            "valid_from": row.valid_from,
            "valid_until": row.valid_until,
            "params": row.params or {},
        }
    )


async def get_active_rules(
    session: AsyncSession, at: datetime | None = None
) -> list[Rule]:
    """Retorna as regras ativas como `Rule` do domínio.

    Filtra `active=True`. Se `at` for informado, também restringe à janela de
    validade vigente naquele instante (regra sem janela vale sempre). O motor
    de scoring ainda revalida a janela por `transaction_date`, então `at` é uma
    pré-filtragem opcional.
    """
    stmt = select(ScoringRule).where(ScoringRule.active.is_(True))
    if at is not None:
        stmt = stmt.where(
            (ScoringRule.valid_from.is_(None)) | (ScoringRule.valid_from <= at)
        ).where(
            (ScoringRule.valid_until.is_(None)) | (ScoringRule.valid_until >= at)
        )
    stmt = stmt.order_by(ScoringRule.priority)

    rows = (await session.execute(stmt)).scalars().all()
    return [_to_domain_rule(row) for row in rows]
