"""Seed da configuração do programa de fidelidade. IDEMPOTENTE.

Rodar (a partir de `fidelidade/`, com o venv):
    python -m scripts.seed_config

Cria/garante:
  - A regra BASE de pontuação (1 ponto por real).
  - Um lote de CUPONS DE TESTE no pool, nas três faixas de recompensa:
      500 pontos  -> R$10
      1000 pontos -> R$25
      2000 pontos -> R$50

================================ AVISO ========================================
 Os cupons criados aqui são DE TESTE (prefixo "TESTE-"). Eles NÃO existem no
 TouchPay e NÃO funcionam no totem real. Antes do go-live, REMOVA os cupons de
 teste (DELETE FROM coupon_pool WHERE code LIKE 'TESTE-%') e cadastre cupons
 REAIS criados no painel da AMLabs.
==============================================================================

DESENHO DA "TABELA DE RECOMPENSAS" (decisão documentada): Opção A — implícita
no pool. Cada CouponPool já carrega `points_cost` e `discount_value`; a "tabela"
é a agregação que `list_available_rewards` já faz. Não criamos um modelo Reward
separado para o MVP (evita complexidade desnecessária); os três níveis ficam
representados pelos cupons no pool.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_engine, get_sessionmaker
from app.db.models import (
    CouponDiscountType,
    CouponPool,
    CouponStatus,
    ScoringRule,
)
from app.domain.scoring import RuleType

BASE_RULE_NAME = "Base: 1 ponto por real"
TEST_PREFIX = "TESTE-"
COUPONS_PER_TIER = 10

# (tag no código, valor do desconto, custo em pontos, pedido mínimo)
# Mínimo proporcional: R$10->R$50, R$25->R$100, R$50->R$150.
REWARD_TIERS = [
    ("R10", Decimal("10.00"), 500, Decimal("50.00")),
    ("R25", Decimal("25.00"), 1000, Decimal("100.00")),
    ("R50", Decimal("50.00"), 2000, Decimal("150.00")),
]


async def _seed_base_rule(session: AsyncSession) -> str:
    existing = (
        await session.execute(
            select(ScoringRule).where(ScoringRule.name == BASE_RULE_NAME)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return "já existia"

    session.add(
        ScoringRule(
            id=uuid.uuid4(),
            name=BASE_RULE_NAME,
            rule_type=RuleType.BASE,
            priority=100,
            active=True,
            valid_from=None,
            valid_until=None,
            params={"points_per_real": 1.0},
        )
    )
    await session.commit()
    return "criada"


async def _seed_test_coupons(session: AsyncSession) -> dict[str, int]:
    created = 0
    existed = 0
    for tag, value, cost, min_order in REWARD_TIERS:
        for i in range(1, COUPONS_PER_TIER + 1):
            code = f"{TEST_PREFIX}{tag}-{i:04d}"
            present = (
                await session.execute(
                    select(CouponPool.id).where(CouponPool.code == code)
                )
            ).scalar_one_or_none()
            if present is not None:
                existed += 1
                continue
            session.add(
                CouponPool(
                    id=uuid.uuid4(),
                    code=code,
                    discount_type=CouponDiscountType.FIXED,
                    discount_value=value,
                    points_cost=cost,
                    min_order_value=min_order,
                    status=CouponStatus.AVAILABLE,
                )
            )
            created += 1

        # Backfill: cupons de teste desta faixa criados ANTES do campo existir
        # ficam com min nulo. Preenche (idempotente: no-op se já preenchido).
        await session.execute(
            update(CouponPool)
            .where(
                CouponPool.code.like(f"{TEST_PREFIX}{tag}-%"),
                CouponPool.min_order_value.is_(None),
            )
            .values(min_order_value=min_order)
        )

    await session.commit()
    return {"created": created, "existed": existed}


async def seed_config(session: AsyncSession) -> dict:
    """Executa o seed e retorna um resumo. Reutilizável em testes."""
    rule_status = await _seed_base_rule(session)
    coupons = await _seed_test_coupons(session)

    total_test_coupons = (
        await session.execute(
            select(func.count())
            .select_from(CouponPool)
            .where(CouponPool.code.like(f"{TEST_PREFIX}%"))
        )
    ).scalar_one()

    return {
        "base_rule": rule_status,
        "coupons_created": coupons["created"],
        "coupons_existed": coupons["existed"],
        "total_test_coupons": total_test_coupons,
    }


async def main() -> None:
    print("=== Seed de configuração (idempotente) ===")
    print("AVISO: cupons criados são DE TESTE (prefixo TESTE-), não funcionam")
    print("no totem real. Remover antes do go-live.\n")

    maker = get_sessionmaker()
    async with maker() as session:
        summary = await seed_config(session)

    print(f"Regra BASE: {summary['base_rule']}")
    print(
        f"Cupons de teste: {summary['coupons_created']} criados, "
        f"{summary['coupons_existed']} já existiam "
        f"(total no pool: {summary['total_test_coupons']})."
    )
    await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
