"""Modelos Pydantic que espelham os schemas do swagger da TouchPay.

Os nomes dos campos seguem EXATAMENTE o contrato da TouchPay (camelCase),
para que a (de)serialização contra a API real seja 1:1. A tradução para o
vocabulário interno do domínio acontece em outra camada, não aqui.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class TransactionItem(BaseModel):
    """Item de uma transação."""

    productId: int
    quantity: int
    price: float


class UserInfo(BaseModel):
    """Dados do consumidor associados à transação.

    `document` é o CPF do cliente.
    """

    phoneNumber: str | None = None
    document: str | None = None
    email: str | None = None


class Transaction(BaseModel):
    """Uma transação (venda) registrada no ponto de venda."""

    uuid: str
    paymentMethod: str | None = None
    totalPrice: float
    userInfo: UserInfo | None = None
    items: list[TransactionItem] | None = None
    pointOfSaleId: int
    date: datetime
    paymentAmount: float


class PaginatedTransactions(BaseModel):
    """Envelope paginado retornado pela listagem de transações."""

    items: list[Transaction] | None = None
    pageIndex: int
    totalPages: int
    totalItems: int
    pageSize: int
    hasPreviousPage: bool
    hasNextPage: bool


class DiscountType(str, Enum):
    """Tipo de desconto aplicado por um cupom."""

    fixed = "Fixed"
    percentage = "Percentage"


class Coupon(BaseModel):
    """Cupom de desconto."""

    discountCouponId: int
    code: str | None = None
    expiresOn: datetime | None = None
    discountType: DiscountType
    discountValue: float
    isValid: bool
