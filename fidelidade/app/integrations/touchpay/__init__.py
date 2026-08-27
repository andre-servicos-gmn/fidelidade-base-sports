"""Camada de integração com a TouchPay.

Tudo o que conhece o "formato TouchPay" vive aqui. O resto da aplicação
deve depender apenas da interface abstrata `TouchPayClient` e dos schemas,
nunca de uma implementação concreta.
"""

from app.integrations.touchpay.client import TouchPayClient
from app.integrations.touchpay.mock_client import MockTouchPayClient
from app.integrations.touchpay.schemas import (
    Coupon,
    DiscountType,
    PaginatedTransactions,
    Transaction,
    TransactionItem,
    UserInfo,
)

__all__ = [
    "TouchPayClient",
    "MockTouchPayClient",
    "Coupon",
    "DiscountType",
    "PaginatedTransactions",
    "Transaction",
    "TransactionItem",
    "UserInfo",
]
