"""Testes do MockTouchPayClient."""

from datetime import datetime

import pytest

from app.integrations.touchpay.mock_client import MockTouchPayClient
from app.integrations.touchpay.schemas import (
    DiscountType,
    PaginatedTransactions,
    Transaction,
)

MIN_DATE = datetime(2026, 1, 1)
MAX_DATE = datetime(2026, 6, 1)


@pytest.fixture
def client() -> MockTouchPayClient:
    return MockTouchPayClient()


async def test_get_transactions_returns_well_formed_transactions(client):
    result = await client.get_transactions(MIN_DATE, MAX_DATE)

    assert isinstance(result, PaginatedTransactions)
    assert result.totalItems >= 3
    assert result.items is not None
    assert len(result.items) == result.totalItems

    for tx in result.items:
        assert isinstance(tx, Transaction)
        assert tx.uuid
        # Datas dentro da janela pedida.
        assert MIN_DATE <= tx.date <= MAX_DATE
        # CPF presente em userInfo.document.
        assert tx.userInfo is not None and tx.userInfo.document
        # Itens com productId entre os produtos simulados.
        assert tx.items
        for item in tx.items:
            assert item.productId in {1, 2, 3, 4, 5}


async def test_pagination_splits_results(client):
    page1 = await client.get_transactions(
        MIN_DATE, MAX_DATE, page=1, page_size=2
    )

    assert page1.pageSize == 2
    assert page1.totalPages == 2
    assert len(page1.items) == 2
    assert page1.hasPreviousPage is False
    assert page1.hasNextPage is True

    page2 = await client.get_transactions(
        MIN_DATE, MAX_DATE, page=2, page_size=2
    )
    assert len(page2.items) == page2.totalItems - 2
    assert page2.hasPreviousPage is True
    assert page2.hasNextPage is False


async def test_pagination_beyond_last_page_is_empty(client):
    result = await client.get_transactions(
        MIN_DATE, MAX_DATE, page=99, page_size=2
    )
    assert result.items == []
    assert result.hasNextPage is False
    assert result.hasPreviousPage is True


async def test_filter_by_cpf(client):
    result = await client.get_transactions(
        MIN_DATE, MAX_DATE, cpf="11111111111"
    )
    assert result.totalItems == 1
    assert result.items[0].userInfo.document == "11111111111"


async def test_get_transaction_by_uuid(client):
    tx = await client.get_transaction("abc-123")
    assert isinstance(tx, Transaction)
    assert tx.uuid == "abc-123"


async def test_get_coupon_valid_with_base_prefix(client):
    coupon = await client.get_coupon("BASE10", point_of_sale_id=1)
    assert coupon.isValid is True
    assert coupon.code == "BASE10"
    assert coupon.discountType in (DiscountType.fixed, DiscountType.percentage)


async def test_get_coupon_invalid_without_base_prefix(client):
    coupon = await client.get_coupon("PROMO10", point_of_sale_id=1)
    assert coupon.isValid is False


async def test_list_products(client):
    products = await client.list_products()
    assert len(products) == 5
    for p in products:
        assert "productId" in p
        assert "description" in p
        assert "categoryName" in p
