"""Testes puros do cálculo de pontos do afiliado (sem banco)."""

from decimal import Decimal

from app.services.affiliate_service import compute_affiliate_points


def test_compute_affiliate_points_basic():
    # 1 real = 1 ponto; 100% do valor.
    assert compute_affiliate_points(Decimal("100"), Decimal("100")) == 100
    # 50% -> metade.
    assert compute_affiliate_points(Decimal("100"), Decimal("50")) == 50
    # 200% -> dobro.
    assert compute_affiliate_points(Decimal("100"), Decimal("200")) == 200


def test_compute_affiliate_points_floor():
    # 99.90 * 100% = 99.90 -> floor 99.
    assert compute_affiliate_points(Decimal("99.90"), Decimal("100")) == 99
    # 33.33 * 50% = 16.665 -> floor 16.
    assert compute_affiliate_points(Decimal("33.33"), Decimal("50")) == 16


def test_compute_affiliate_points_zero_rate():
    # Taxa 0 (padrão) -> afiliado não ganha nada.
    assert compute_affiliate_points(Decimal("250.00"), Decimal("0")) == 0
