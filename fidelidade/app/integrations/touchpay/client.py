"""Interface abstrata do cliente TouchPay.

O resto da aplicação depende apenas desta interface. Trocar o mock por um
cliente HTTP real (ou qualquer outra implementação) é só fornecer outra
subclasse de `TouchPayClient` — nenhum código de domínio precisa mudar.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from app.integrations.touchpay.schemas import (
    Coupon,
    PaginatedTransactions,
    Transaction,
)


class TouchPayClient(ABC):
    """Contrato para acesso aos dados da TouchPay."""

    @abstractmethod
    async def get_transactions(
        self,
        min_date: datetime,
        max_date: datetime,
        page: int = 1,
        page_size: int = 50,
        cpf: str | None = None,
        point_of_sale_id: int | None = None,
    ) -> PaginatedTransactions:
        """Lista transações dentro da janela [min_date, max_date], paginadas.

        `cpf` e `point_of_sale_id` são filtros opcionais.
        """
        ...

    @abstractmethod
    async def get_transaction(self, uuid: str) -> Transaction:
        """Retorna uma única transação pelo seu `uuid`."""
        ...

    @abstractmethod
    async def get_coupon(self, code: str, point_of_sale_id: int) -> Coupon:
        """Resolve um cupom pelo `code` em um ponto de venda."""
        ...

    @abstractmethod
    async def list_products(
        self,
        page: int = 1,
        page_size: int = 1000,
    ) -> list[dict]:
        """Lista o catálogo de produtos.

        Retorna dicts crus por enquanto — o schema de produto ainda não foi
        modelado formalmente.
        """
        ...

    @abstractmethod
    async def get_points_of_sale(self) -> list[dict]:
        """Lista os pontos de venda (PDVs) disponíveis.

        Leitura usada para descobrir o `pointOfSaleId` da Base Sports.
        Retorna dicts crus (tolerante a variações de schema).
        """
        ...
