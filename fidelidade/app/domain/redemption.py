"""Tipos de domínio do resgate de cupons (puros, sem I/O).

Apenas erros tipados e o resultado de um resgate. A lógica transacional vive em
`app.services.redemption_service`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


class RedemptionError(Exception):
    """Base de todos os erros de resgate."""


class CustomerNotFoundError(RedemptionError):
    """Cliente inexistente."""

    def __init__(self, customer_id: object) -> None:
        self.customer_id = customer_id
        super().__init__(f"Cliente não encontrado: {customer_id}")


class InsufficientPointsError(RedemptionError):
    """Saldo insuficiente para o custo da recompensa.

    Carrega o saldo atual e o custo para a camada de cima explicar ao cliente.
    """

    def __init__(self, balance: int, cost: int) -> None:
        self.balance = balance
        self.cost = cost
        super().__init__(
            f"Saldo insuficiente: saldo={balance}, custo={cost}"
        )


class NoCouponAvailableError(RedemptionError):
    """Não há cupom AVAILABLE para a recompensa pedida."""

    def __init__(self, reward_id: str) -> None:
        self.reward_id = reward_id
        super().__init__(f"Sem cupom disponível para a recompensa: {reward_id}")


@dataclass(frozen=True)
class RedemptionResult:
    """Resultado de um resgate bem-sucedido."""

    coupon_code: str
    discount_type: str
    discount_value: Decimal
    points_spent: int
    balance_after: int
    expires_at: datetime
    # Pedido mínimo (R$) do cupom, ou None se não houver. Informativo: a
    # confirmação repete essa condição para o cliente.
    min_order_value: Decimal | None = None
