"""Schemas Pydantic (request/response) da API administrativa.

Inclui a validação dos `params` de cada tipo de regra: o painel não pode criar
uma regra quebrada. Params inválidos -> ValidationError -> HTTP 422.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.db.models import (
    AffiliateType,
    CouponDiscountType,
    CouponStatus,
    LedgerEntryType,
)
from app.domain.scoring import RuleType


# --------------------------------------------------------------------------- #
# Auth                                                                         #
# --------------------------------------------------------------------------- #
class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# --------------------------------------------------------------------------- #
# Regras                                                                       #
# --------------------------------------------------------------------------- #
def _require_number(params: dict, key: str) -> None:
    value = params.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"params.{key} é obrigatório e deve ser numérico.")


def _require_str(params: dict, key: str) -> None:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"params.{key} é obrigatório e deve ser texto.")


def _require_int(params: dict, key: str) -> None:
    value = params.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"params.{key} é obrigatório e deve ser inteiro.")


def validate_rule_params(rule_type: RuleType, params: dict) -> None:
    """Valida os params conforme o rule_type. Levanta ValueError se inválido."""
    if rule_type is RuleType.BASE:
        _require_number(params, "points_per_real")
    elif rule_type is RuleType.CATEGORY_MULTIPLIER:
        _require_str(params, "category")
        _require_number(params, "multiplier")
    elif rule_type is RuleType.PRODUCT_MULTIPLIER:
        _require_int(params, "product_id")
        _require_number(params, "multiplier")
    elif rule_type is RuleType.CATEGORY_BONUS_PERCENT:
        _require_str(params, "category")
        _require_number(params, "percent")


class RuleCreate(BaseModel):
    name: str
    rule_type: RuleType
    priority: int = 0
    active: bool = True
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_params(self) -> "RuleCreate":
        validate_rule_params(self.rule_type, self.params)
        return self


class RuleUpdate(RuleCreate):
    """Mesma forma e validação do create (substituição completa)."""


class RuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    rule_type: RuleType
    priority: int
    active: bool
    valid_from: datetime | None
    valid_until: datetime | None
    params: dict[str, Any]
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- #
# Cupons                                                                       #
# --------------------------------------------------------------------------- #
class CouponBatchCreate(BaseModel):
    codes: list[str] = Field(min_length=1)
    discount_type: CouponDiscountType
    discount_value: Decimal
    points_cost: int
    # Pedido mínimo (R$) informado no cupom real do TouchPay. Opcional.
    min_order_value: Decimal | None = None
    expires_at: datetime | None = None


class CouponResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    discount_type: CouponDiscountType
    discount_value: Decimal
    points_cost: int
    min_order_value: Decimal | None
    status: CouponStatus
    allocated_to_customer_id: uuid.UUID | None
    allocated_at: datetime | None
    expires_at: datetime | None
    created_at: datetime


class CouponBatchResult(BaseModel):
    created: list[str]
    skipped: list[str]
    warning: str


class RewardSummaryRow(BaseModel):
    discount_type: str
    discount_value: float
    points_cost: int
    min_order_value: float | None = None
    available: int = 0
    allocated: int = 0
    used: int = 0
    expired: int = 0


class CouponListResponse(BaseModel):
    items: list[CouponResponse]
    summary: list[RewardSummaryRow]


# --------------------------------------------------------------------------- #
# Clientes                                                                     #
# --------------------------------------------------------------------------- #
class CustomerResponse(BaseModel):
    id: uuid.UUID
    cpf_masked: str
    phone_masked: str | None
    balance: int
    created_at: datetime


class CustomerListResponse(BaseModel):
    """Página da listagem de clientes (mesma forma de linha da busca)."""

    items: list[CustomerResponse]
    total: int
    page: int
    page_size: int


class LedgerEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sequence: int
    created_at: datetime
    entry_type: LedgerEntryType
    points: int
    balance_after: int
    description: str | None


class CustomerCouponResponse(BaseModel):
    code: str
    discount_type: str
    discount_value: float
    points_cost: int
    min_order_value: float | None = None
    status: str
    allocated_at: datetime | None
    expires_at: datetime | None


# --------------------------------------------------------------------------- #
# Afiliados                                                                    #
# --------------------------------------------------------------------------- #
# Código: 3–32 caracteres, letras/números/hífen, sempre em MAIÚSCULAS.
_AFFILIATE_CODE_RE = re.compile(r"^[A-Z0-9-]{3,32}$")


class AffiliateCreate(BaseModel):
    name: str
    affiliate_type: AffiliateType
    code: str
    # Taxa de pontos do afiliado por compra, em % (100 = 100%). Base 1 real = 1 ponto.
    points_rate: Decimal = Decimal(0)
    contact: str | None = None
    notes: str | None = None
    active: bool = True

    @field_validator("points_rate")
    @classmethod
    def _check_points_rate(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("A taxa de pontos não pode ser negativa.")
        return value

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Nome é obrigatório.")
        return value

    @field_validator("code")
    @classmethod
    def _check_code(cls, value: str) -> str:
        value = value.strip().upper()
        if not _AFFILIATE_CODE_RE.match(value):
            raise ValueError(
                "Código deve ter 3 a 32 caracteres: letras, números ou hífen."
            )
        return value


class AffiliateUpdate(AffiliateCreate):
    """Mesma forma e validação do create (substituição completa)."""


class AffiliateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    affiliate_type: AffiliateType
    code: str
    points_rate: Decimal
    contact: str | None
    notes: str | None
    active: bool
    created_at: datetime
    updated_at: datetime


class AffiliateStatsResponse(BaseModel):
    """Métricas agregadas de um afiliado (atribuições por compra)."""

    affiliate_id: uuid.UUID
    customers: int
    purchases: int
    points: int
    affiliate_points: int
