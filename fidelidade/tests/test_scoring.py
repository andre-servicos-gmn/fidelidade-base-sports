"""Testes do motor de pontuação (puro, sem I/O)."""

from datetime import datetime

import pytest

from app.domain.scoring import (
    Rule,
    RuleType,
    ScoringContext,
    ScoringItem,
    calculate_points,
)

DATE = datetime(2026, 6, 5, 12, 0, 0)


def _ctx(items: list[ScoringItem], when: datetime = DATE) -> ScoringContext:
    total = sum(i.total_price for i in items)
    return ScoringContext(items=items, total_amount=total, transaction_date=when)


def _base_rule(points_per_real: float = 1.0, **kw) -> Rule:
    return Rule(
        id=kw.pop("id", "base"),
        name="Base",
        rule_type=RuleType.BASE,
        priority=kw.pop("priority", 0),
        params={"points_per_real": points_per_real},
        **kw,
    )


def _sum_breakdown(result) -> float:
    return sum(b["points"] for b in result.breakdown)


# --------------------------------------------------------------------------- #
def test_base_only():
    item = ScoringItem(1, "Raquetes", 1, 100.0, 100.0)
    result = calculate_points(_ctx([item]), [_base_rule(1.0)])

    assert result.total_points == 100
    base_entries = [b for b in result.breakdown if b["rule_type"] == "BASE"]
    assert len(base_entries) == 1
    assert base_entries[0]["points"] == 100.0


def test_category_multiplier():
    # Raquete de R$200, base 1, multiplicador 2x -> 400 para esse item.
    item = ScoringItem(1, "Raquetes", 1, 200.0, 200.0)
    mult = Rule(
        id="m1",
        name="Dobro em Raquetes",
        rule_type=RuleType.CATEGORY_MULTIPLIER,
        priority=10,
        params={"category": "Raquetes", "multiplier": 2.0},
    )
    result = calculate_points(_ctx([item]), [_base_rule(1.0), mult])

    assert result.total_points == 400
    assert _sum_breakdown(result) == pytest.approx(400)


def test_product_multiplier():
    # Produto 42 de R$100, base 1, multiplicador 3x -> 300.
    item = ScoringItem(42, "Bolas", 1, 100.0, 100.0)
    other = ScoringItem(7, "Bolas", 1, 50.0, 50.0)
    mult = Rule(
        id="p1",
        name="Triplo no produto 42",
        rule_type=RuleType.PRODUCT_MULTIPLIER,
        priority=10,
        params={"product_id": 42, "multiplier": 3.0},
    )
    result = calculate_points(_ctx([item, other]), [_base_rule(1.0), mult])

    # 300 (produto 42) + 50 (outro item, só base) = 350.
    assert result.total_points == 350
    assert _sum_breakdown(result) == pytest.approx(350)


def test_category_bonus_percent():
    # Bola de R$50, base 1, bônus 10% -> 55.
    item = ScoringItem(2, "Bolas", 1, 50.0, 50.0)
    bonus = Rule(
        id="b1",
        name="Bônus Bolas",
        rule_type=RuleType.CATEGORY_BONUS_PERCENT,
        priority=20,
        params={"category": "Bolas", "percent": 10.0},
    )
    result = calculate_points(_ctx([item]), [_base_rule(1.0), bonus])

    assert result.total_points == 55
    assert _sum_breakdown(result) == pytest.approx(55)


def test_stacking_base_multiplier_and_bonus():
    # Raquete R$100: base 1 -> 100; ×2 -> 200; +10% sobre 200 -> 220.
    item = ScoringItem(1, "Raquetes", 1, 100.0, 100.0)
    mult = Rule(
        id="m1",
        name="Dobro Raquetes",
        rule_type=RuleType.CATEGORY_MULTIPLIER,
        priority=10,
        params={"category": "Raquetes", "multiplier": 2.0},
    )
    bonus = Rule(
        id="b1",
        name="Bônus Raquetes",
        rule_type=RuleType.CATEGORY_BONUS_PERCENT,
        priority=20,
        params={"category": "Raquetes", "percent": 10.0},
    )
    result = calculate_points(_ctx([item]), [_base_rule(1.0), mult, bonus])

    assert result.total_points == 220
    # Breakdown: base 100, mult +100, bonus +20, arredondamento 0.
    types = [b["rule_type"] for b in result.breakdown]
    assert types == ["BASE", "CATEGORY_MULTIPLIER", "CATEGORY_BONUS_PERCENT",
                     "ROUNDING"]
    assert _sum_breakdown(result) == pytest.approx(result.total_points)


def test_two_multipliers_stack_multiplicatively():
    # Mesmo item atingido por 2× (categoria) e 3× (produto) -> 6×.
    item = ScoringItem(42, "Raquetes", 1, 100.0, 100.0)
    cat = Rule(
        id="m_cat",
        name="2x Raquetes",
        rule_type=RuleType.CATEGORY_MULTIPLIER,
        priority=10,
        params={"category": "Raquetes", "multiplier": 2.0},
    )
    prod = Rule(
        id="m_prod",
        name="3x Produto 42",
        rule_type=RuleType.PRODUCT_MULTIPLIER,
        priority=11,
        params={"product_id": 42, "multiplier": 3.0},
    )
    result = calculate_points(_ctx([item]), [_base_rule(1.0), cat, prod])

    assert result.total_points == 600  # 100 × 2 × 3
    assert _sum_breakdown(result) == pytest.approx(600)


def test_category_none_does_not_match_category_multiplier():
    item = ScoringItem(1, None, 1, 100.0, 100.0)
    mult = Rule(
        id="m1",
        name="2x Raquetes",
        rule_type=RuleType.CATEGORY_MULTIPLIER,
        priority=10,
        params={"category": "Raquetes", "multiplier": 2.0},
    )
    result = calculate_points(_ctx([item]), [_base_rule(1.0), mult])

    # Item sem categoria não casa: só pontos base.
    assert result.total_points == 100


def test_seasonal_rule_inside_and_outside_window():
    item = ScoringItem(1, "Raquetes", 1, 100.0, 100.0)
    seasonal = Rule(
        id="promo",
        name="Promo de Junho",
        rule_type=RuleType.CATEGORY_MULTIPLIER,
        priority=10,
        valid_from=datetime(2026, 6, 1),
        valid_until=datetime(2026, 6, 30, 23, 59, 59),
        params={"category": "Raquetes", "multiplier": 2.0},
    )
    rules = [_base_rule(1.0), seasonal]

    inside = calculate_points(_ctx([item], datetime(2026, 6, 15)), rules)
    outside = calculate_points(_ctx([item], datetime(2026, 7, 15)), rules)

    assert inside.total_points == 200   # promo aplicada
    assert outside.total_points == 100  # promo fora da janela -> só base


def test_no_base_rule_yields_zero_with_warning():
    item = ScoringItem(1, "Raquetes", 1, 100.0, 100.0)
    mult = Rule(
        id="m1",
        name="2x Raquetes",
        rule_type=RuleType.CATEGORY_MULTIPLIER,
        priority=10,
        params={"category": "Raquetes", "multiplier": 2.0},
    )
    result = calculate_points(_ctx([item]), [mult])

    assert result.total_points == 0
    assert any(b["rule_type"] == "WARNING" for b in result.breakdown)


def test_inactive_base_rule_is_ignored():
    item = ScoringItem(1, "Raquetes", 1, 100.0, 100.0)
    result = calculate_points(_ctx([item]), [_base_rule(1.0, active=False)])
    assert result.total_points == 0


def test_multiple_base_rules_highest_priority_wins():
    item = ScoringItem(1, "Raquetes", 1, 100.0, 100.0)
    low_priority = _base_rule(5.0, id="base_lo", priority=100)
    high_priority = _base_rule(1.0, id="base_hi", priority=1)
    result = calculate_points(
        _ctx([item]), [low_priority, high_priority]
    )
    # priority=1 (maior prioridade) vence -> points_per_real=1.0 -> 100.
    assert result.total_points == 100


def test_breakdown_sums_to_total_points():
    items = [
        ScoringItem(1, "Raquetes", 1, 199.99, 199.99),
        ScoringItem(2, "Bolas", 3, 9.90, 29.70),
        ScoringItem(3, "Acessorios", 2, 12.50, 25.00),
    ]
    mult = Rule(
        id="m1",
        name="2x Raquetes",
        rule_type=RuleType.CATEGORY_MULTIPLIER,
        priority=10,
        params={"category": "Raquetes", "multiplier": 2.0},
    )
    bonus = Rule(
        id="b1",
        name="Bônus 7.5% Bolas",
        rule_type=RuleType.CATEGORY_BONUS_PERCENT,
        priority=20,
        params={"category": "Bolas", "percent": 7.5},
    )
    result = calculate_points(_ctx(items), [_base_rule(1.0), mult, bonus])

    assert _sum_breakdown(result) == pytest.approx(result.total_points)
    # E o total bruto sofreu floor.
    assert result.total_points == int(result.raw_total) or (
        result.total_points <= result.raw_total
    )
