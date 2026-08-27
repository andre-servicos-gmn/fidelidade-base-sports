"""Converte uma `Transaction` da TouchPay num `ScoringContext` do motor.

Função PURA, sem I/O. A TouchPay traz `productId` e preço nos itens, mas NÃO a
categoria — esta vem do catálogo (`list_products`). Por isso recebemos um dict
`product_id -> category` para enriquecer os itens.
"""

from __future__ import annotations

from app.domain.scoring import ScoringContext, ScoringItem
from app.integrations.touchpay.schemas import Transaction


def to_scoring_context(
    transaction: Transaction,
    product_categories: dict[int, str],
) -> ScoringContext:
    """Mapeia `Transaction` -> `ScoringContext`.

    - `unit_price`  = `item.price` (preço unitário da TouchPay).
    - `total_price` = `item.price * item.quantity`.
    - `category`    = `product_categories[product_id]`, ou **None** se o produto
      não estiver no catálogo (categoria ausente vira None, e itens com
      categoria None não casam com regras por categoria).
    - `total_amount` = `transaction.totalPrice`.
    - `transaction_date` = `transaction.date`.
    """
    items: list[ScoringItem] = []
    for item in transaction.items or []:
        category = product_categories.get(item.productId)
        items.append(
            ScoringItem(
                product_id=item.productId,
                category=category,
                quantity=item.quantity,
                unit_price=item.price,
                total_price=item.price * item.quantity,
            )
        )

    return ScoringContext(
        items=items,
        total_amount=transaction.totalPrice,
        transaction_date=transaction.date,
    )
