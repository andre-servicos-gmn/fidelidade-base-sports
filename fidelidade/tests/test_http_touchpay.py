"""Testes do HttpTouchPayClient com httpx mockado (NÃO bate na API real).

Verificam a montagem de params, o header de auth, o parsing tolerante e o
tratamento de erro sem vazar token. Tudo offline via httpx.MockTransport.
"""

from datetime import datetime

import httpx
import pytest

from app.integrations.touchpay.http_client import (
    HttpTouchPayClient,
    TouchPayApiError,
)
from app.integrations.touchpay.schemas import (
    Coupon,
    PaginatedTransactions,
    Transaction,
)

TOKEN = "secret-jwt-token-should-not-leak"
BASE = "https://api.touchpay.test"

MIN_DATE = datetime(2026, 1, 1, 0, 0, 0)
MAX_DATE = datetime(2026, 2, 1, 0, 0, 0)

_TX_JSON = {
    "uuid": "u-123",
    "paymentMethod": "Pix",
    "totalPrice": 100.0,
    "userInfo": {"document": "12345678901", "phoneNumber": None, "email": None},
    "items": [{"productId": 1, "quantity": 2, "price": 50.0, "EXTRA": "x"}],
    "pointOfSaleId": 5,
    "date": "2026-01-15T10:00:00",
    "paymentAmount": 100.0,
    "UNKNOWN_FIELD": "ignore me",  # campo extra do swagger -> tolerante
}

_PAGINATED_JSON = {
    "items": [_TX_JSON],
    "pageIndex": 1,
    "totalPages": 3,
    "totalItems": 5,
    "pageSize": 20,
    "hasPreviousPage": False,
    "hasNextPage": True,
    "ENVELOPE_EXTRA": "ok",
}

_COUPON_JSON = {
    "discountCouponId": 7,
    "code": "BASE10",
    "expiresOn": None,
    "discountType": "Percentage",
    "discountValue": 10.0,
    "isValid": True,
}


def _make_client(handler) -> HttpTouchPayClient:
    transport = httpx.MockTransport(handler)
    return HttpTouchPayClient(base_url=BASE, token=TOKEN, transport=transport)


async def test_get_transactions_builds_params_and_parses():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json=_PAGINATED_JSON)

    client = _make_client(handler)
    result = await client.get_transactions(
        MIN_DATE, MAX_DATE, page=2, page_size=20,
        cpf="12345678901", point_of_sale_id=5,
    )
    await client.aclose()

    req = captured["request"]
    assert req.url.path == "/api/public/transactions"
    assert req.headers["Authorization"] == f"Bearer {TOKEN}"
    params = req.url.params
    assert params["MinDate"] == MIN_DATE.isoformat()
    assert params["MaxDate"] == MAX_DATE.isoformat()
    assert params["Page"] == "2"
    assert params["PageSize"] == "20"
    assert params["Cpf"] == "12345678901"
    assert params["PointOfSaleId"] == "5"

    assert isinstance(result, PaginatedTransactions)
    assert result.totalItems == 5
    assert result.hasNextPage is True
    assert result.items[0].uuid == "u-123"
    assert result.items[0].items[0].productId == 1


async def test_get_transactions_omits_optional_params():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json=_PAGINATED_JSON)

    client = _make_client(handler)
    await client.get_transactions(MIN_DATE, MAX_DATE)
    await client.aclose()

    params = captured["request"].url.params
    assert "Cpf" not in params
    assert "PointOfSaleId" not in params


async def test_get_transaction_parses():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/public/transactions/u-123"
        return httpx.Response(200, json=_TX_JSON)

    client = _make_client(handler)
    tx = await client.get_transaction("u-123")
    await client.aclose()

    assert isinstance(tx, Transaction)
    assert tx.uuid == "u-123"
    assert tx.userInfo.document == "12345678901"


async def test_get_coupon_builds_url_and_parses():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json=_COUPON_JSON)

    client = _make_client(handler)
    coupon = await client.get_coupon("BASE10", point_of_sale_id=5)
    await client.aclose()

    req = captured["request"]
    assert req.url.path == "/api/public/cart/coupon/BASE10"
    assert req.url.params["pointOfSaleId"] == "5"
    assert isinstance(coupon, Coupon)
    assert coupon.isValid is True
    assert coupon.discountType.value == "Percentage"


async def test_list_products_envelope_and_tolerant():
    payload = {
        "items": [
            {"productId": 1, "description": "Raquete", "categoryName": "Raquetes"},
            {"productId": 2, "description": "Bola", "categoryName": None, "X": 1},
        ],
        "pageIndex": 1,
        "totalPages": 1,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/public/products/v2"
        assert request.url.params["Page"] == "1"
        return httpx.Response(200, json=payload)

    client = _make_client(handler)
    products = await client.list_products(page=1, page_size=50)
    await client.aclose()

    assert len(products) == 2
    assert products[0]["categoryName"] == "Raquetes"
    assert products[1]["X"] == 1  # campo extra preservado no dict cru


async def test_list_products_raw_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"productId": 9}])

    client = _make_client(handler)
    products = await client.list_products()
    await client.aclose()
    assert products == [{"productId": 9}]


async def test_get_points_of_sale():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/public/pointsOfSale"
        return httpx.Response(
            200, json=[{"id": 1, "name": "Base SP", "state": "SP"}]
        )

    client = _make_client(handler)
    pos = await client.get_points_of_sale()
    await client.aclose()
    assert pos[0]["id"] == 1


async def test_non_2xx_raises_without_leaking_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    client = _make_client(handler)
    with pytest.raises(TouchPayApiError) as exc:
        await client.get_transaction("u-1")
    await client.aclose()

    msg = str(exc.value)
    assert "500" in msg
    assert TOKEN not in msg  # o token NUNCA aparece no erro
