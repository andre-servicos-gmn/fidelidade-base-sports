"""Implementação em memória do `TouchPayClient` para desenvolvimento e testes.

Sem rede, sem banco — tudo determinístico e em memória. A ideia é poder
desenvolver e testar todo o domínio de fidelidade antes de existir um cliente
TouchPay real.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from math import ceil

from app.integrations.touchpay.client import TouchPayClient
from app.integrations.touchpay.schemas import (
    Coupon,
    DiscountType,
    PaginatedTransactions,
    Transaction,
    TransactionItem,
    UserInfo,
)

# Catálogo fake de produtos, referenciado pelos itens das transações.
_FAKE_PRODUCTS: list[dict] = [
    {"productId": 1, "description": "Raquete Pro Carbon", "categoryName": "Raquetes"},
    {"productId": 2, "description": "Bola de Padel (3un)", "categoryName": "Bolas"},
    {"productId": 3, "description": "Grip Overgrip", "categoryName": "Acessórios"},
    {"productId": 4, "description": "Mochila Térmica", "categoryName": "Acessórios"},
    {"productId": 5, "description": "Bola de Tênis (4un)", "categoryName": "Bolas"},
]


def _demo_transaction(when: datetime) -> Transaction | None:
    """Compra de DEMO opcional, definida por variáveis de ambiente.

    Serve para ensaiar o fluxo ponta a ponta com um CPF/telefone REAIS (o do
    testador) sem colocar dado pessoal no código: os valores vivem só no
    ambiente do processo. Sem `DEMO_PURCHASE_CPF` definido, nada é adicionado e
    o mock se comporta exatamente como antes (testes não são afetados).

    Por que aqui, e não num script avulso: o estado da conversa
    (`AWAITING_AFFILIATE_CODE`) é guardado em memória do PROCESSO. Só a compra
    que entra pelo worker rodando DENTRO da API (`RUN_WORKER_IN_APP=true`)
    grava esse estado no mesmo processo que atende o webhook — que é o que faz
    a resposta do cliente com o código de afiliado ser reconhecida.

    Variáveis (todas opcionais menos a primeira):
        DEMO_PURCHASE_CPF     CPF do comprador (só dígitos). Vazio = desligado.
        DEMO_PURCHASE_PHONE   Telefone para o WhatsApp. Vazio = cliente sem
                              telefone (não recebe a pergunta de afiliado).
        DEMO_PURCHASE_AMOUNT  Valor total em reais (default 1000).
        DEMO_PURCHASE_UUID    Identificador da compra. É a chave de
                              idempotência: repetir o mesmo uuid NÃO credita
                              pontos de novo.
        DEMO_PURCHASE_POS     pointOfSaleId (default 1).
    """
    cpf = os.getenv("DEMO_PURCHASE_CPF", "").strip()
    if not cpf:
        return None

    amount = float(os.getenv("DEMO_PURCHASE_AMOUNT", "1000"))
    phone = os.getenv("DEMO_PURCHASE_PHONE", "").strip() or None

    return Transaction(
        uuid=os.getenv("DEMO_PURCHASE_UUID", "DEMO-PRIMEIRA-COMPRA"),
        paymentMethod="CreditCard",
        totalPrice=amount,
        paymentAmount=amount,
        pointOfSaleId=int(os.getenv("DEMO_PURCHASE_POS", "1")),
        date=when,
        userInfo=UserInfo(phoneNumber=phone, document=cpf, email=None),
        items=[TransactionItem(productId=1, quantity=1, price=amount)],
    )


class MockTouchPayClient(TouchPayClient):
    """Cliente TouchPay fake, 100% em memória."""

    def _build_pool(
        self,
        min_date: datetime,
        max_date: datetime,
    ) -> list[Transaction]:
        """Gera o conjunto completo de transações fake dentro da janela.

        As datas são distribuídas dentro de [min_date, max_date] para que o
        resultado sempre respeite a janela pedida.
        """
        span = max_date - min_date

        def at(fraction: float) -> datetime:
            return min_date + span * fraction

        pool = [
            Transaction(
                uuid="11111111-1111-1111-1111-111111111111",
                paymentMethod="CreditCard",
                totalPrice=350.0,
                paymentAmount=350.0,
                pointOfSaleId=1,
                date=at(0.1),
                userInfo=UserInfo(
                    phoneNumber="+5511990000001",
                    document="11111111111",
                    email="ana@example.com",
                ),
                items=[
                    TransactionItem(productId=1, quantity=1, price=300.0),
                    TransactionItem(productId=3, quantity=2, price=25.0),
                ],
            ),
            Transaction(
                uuid="22222222-2222-2222-2222-222222222222",
                paymentMethod="Pix",
                totalPrice=90.0,
                paymentAmount=90.0,
                pointOfSaleId=1,
                date=at(0.4),
                userInfo=UserInfo(
                    phoneNumber="+5511990000002",
                    document="22222222222",
                    email="bruno@example.com",
                ),
                items=[
                    TransactionItem(productId=2, quantity=3, price=30.0),
                ],
            ),
            Transaction(
                uuid="33333333-3333-3333-3333-333333333333",
                paymentMethod="DebitCard",
                totalPrice=180.0,
                paymentAmount=180.0,
                pointOfSaleId=2,
                date=at(0.7),
                userInfo=UserInfo(
                    phoneNumber="+5511990000003",
                    document="33333333333",
                    email="carla@example.com",
                ),
                items=[
                    TransactionItem(productId=2, quantity=2, price=30.0),
                    TransactionItem(productId=3, quantity=1, price=25.0),
                    TransactionItem(productId=1, quantity=1, price=95.0),
                ],
            ),
            Transaction(
                uuid="44444444-4444-4444-4444-444444444444",
                paymentMethod="Cash",
                totalPrice=60.0,
                paymentAmount=60.0,
                pointOfSaleId=2,
                date=at(0.95),
                userInfo=UserInfo(
                    phoneNumber="+5511990000004",
                    document="44444444444",
                    email="diego@example.com",
                ),
                items=[
                    TransactionItem(productId=3, quantity=1, price=25.0),
                    TransactionItem(productId=2, quantity=1, price=35.0),
                ],
            ),
        ]

        # Compra de demo (dirigida por ambiente), datada perto do fim da janela
        # para parecer "acabou de acontecer". Ver `_demo_transaction`.
        demo = _demo_transaction(at(0.99))
        if demo is not None:
            pool.append(demo)

        return pool

    async def get_transactions(
        self,
        min_date: datetime,
        max_date: datetime,
        page: int = 1,
        page_size: int = 50,
        cpf: str | None = None,
        point_of_sale_id: int | None = None,
    ) -> PaginatedTransactions:
        pool = self._build_pool(min_date, max_date)

        if cpf is not None:
            pool = [
                t for t in pool if t.userInfo and t.userInfo.document == cpf
            ]
        if point_of_sale_id is not None:
            pool = [t for t in pool if t.pointOfSaleId == point_of_sale_id]

        total_items = len(pool)
        total_pages = max(1, ceil(total_items / page_size)) if page_size else 1

        if page > total_pages:
            page_items: list[Transaction] = []
        else:
            start = (page - 1) * page_size
            page_items = pool[start : start + page_size]

        return PaginatedTransactions(
            items=page_items,
            pageIndex=page,
            totalPages=total_pages,
            totalItems=total_items,
            pageSize=page_size,
            hasPreviousPage=page > 1,
            hasNextPage=page < total_pages,
        )

    async def get_transaction(self, uuid: str) -> Transaction:
        # Janela arbitrária só para gerar uma data plausível.
        now = datetime(2026, 6, 5, 12, 0, 0)
        return Transaction(
            uuid=uuid,
            paymentMethod="CreditCard",
            totalPrice=350.0,
            paymentAmount=350.0,
            pointOfSaleId=1,
            date=now - timedelta(days=1),
            userInfo=UserInfo(
                phoneNumber="+5511990000001",
                document="11111111111",
                email="ana@example.com",
            ),
            items=[
                TransactionItem(productId=1, quantity=1, price=300.0),
                TransactionItem(productId=3, quantity=2, price=25.0),
            ],
        )

    async def get_coupon(self, code: str, point_of_sale_id: int) -> Coupon:
        is_valid = code.startswith("BASE")
        return Coupon(
            discountCouponId=1,
            code=code,
            expiresOn=datetime(2030, 1, 1, 0, 0, 0),
            discountType=DiscountType.percentage,
            discountValue=10.0,
            isValid=is_valid,
        )

    async def list_products(
        self,
        page: int = 1,
        page_size: int = 1000,
    ) -> list[dict]:
        start = (page - 1) * page_size
        return _FAKE_PRODUCTS[start : start + page_size]

    async def get_points_of_sale(self) -> list[dict]:
        return [
            {"id": 1, "name": "Base Sports - Shopping", "state": "SP"},
            {"id": 2, "name": "Base Sports - Centro", "state": "RJ"},
        ]
