"""Cliente TouchPay HTTP real — SOMENTE LEITURA (GET).

ATENÇÃO OPERACIONAL: a API TouchPay NÃO tem sandbox. Toda chamada bate na
produção real do cliente. Por isso este cliente implementa APENAS métodos de
leitura (GET). Nenhuma escrita (POST/PUT/DELETE) é feita — não criamos, alteramos
nem removemos nada na produção. Se a interface ganhar métodos de escrita no
futuro, eles devem levantar NotImplementedError aqui (de propósito).

Autenticação: Bearer token lido da config (`touchpay_token`). O token NUNCA é
hardcoded nem aparece em mensagens de erro/log.

Parsing tolerante: os schemas Pydantic já ignoram campos extras por padrão
(Pydantic v2), então respostas com campos a mais que o swagger não mostrou não
quebram o parsing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from app.integrations.touchpay.client import TouchPayClient
from app.integrations.touchpay.schemas import (
    Coupon,
    PaginatedTransactions,
    Transaction,
)


class TouchPayApiError(Exception):
    """Erro de uma chamada à API TouchPay (sem vazar token/credenciais)."""


def _as_list(payload: Any) -> list[dict]:
    """Extrai uma lista de dicts de uma resposta tolerante.

    Aceita tanto uma lista crua quanto um envelope paginado `{"items": [...]}`.
    """
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        items = payload.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


class HttpTouchPayClient(TouchPayClient):
    """Implementação real da interface TouchPay contra a API de produção."""

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_client = client is None
        if client is not None:
            self._client = client
        else:
            # Reusa a conexão entre chamadas (não abre uma por request).
            self._client = httpx.AsyncClient(
                base_url=base_url.rstrip("/"),
                headers={"Authorization": f"Bearer {token}"},
                timeout=timeout,
                transport=transport,
            )

    # -- ciclo de vida ----------------------------------------------------- #
    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "HttpTouchPayClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # -- helper de request ------------------------------------------------- #
    async def _get(self, path: str, params: dict | None = None) -> Any:
        """GET com tratamento de erro claro (sem vazar token)."""
        try:
            response = await self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            # Não inclui headers (onde está o token) — só o tipo e o path.
            raise TouchPayApiError(
                f"Falha de rede ao chamar TouchPay GET {path}: "
                f"{type(exc).__name__}"
            ) from exc

        if not response.is_success:
            # response.text pode conter detalhe da API, mas nunca o nosso token
            # (que vai só no header de request).
            raise TouchPayApiError(
                f"TouchPay GET {path} retornou HTTP {response.status_code}"
            )
        return response.json()

    # -- leitura ----------------------------------------------------------- #
    async def get_transactions(
        self,
        min_date: datetime,
        max_date: datetime,
        page: int = 1,
        page_size: int = 50,
        cpf: str | None = None,
        point_of_sale_id: int | None = None,
    ) -> PaginatedTransactions:
        params: dict[str, Any] = {
            "MinDate": min_date.isoformat(),
            "MaxDate": max_date.isoformat(),
            "Page": page,
            "PageSize": page_size,
        }
        if cpf is not None:
            params["Cpf"] = cpf
        if point_of_sale_id is not None:
            params["PointOfSaleId"] = point_of_sale_id

        data = await self._get("/api/public/transactions", params=params)
        return PaginatedTransactions.model_validate(data)

    async def get_transaction(self, uuid: str) -> Transaction:
        data = await self._get(f"/api/public/transactions/{uuid}")
        return Transaction.model_validate(data)

    async def get_coupon(self, code: str, point_of_sale_id: int) -> Coupon:
        data = await self._get(
            f"/api/public/cart/coupon/{code}",
            params={"pointOfSaleId": point_of_sale_id},
        )
        return Coupon.model_validate(data)

    async def list_products(
        self,
        page: int = 1,
        page_size: int = 1000,
    ) -> list[dict]:
        data = await self._get(
            "/api/public/products/v2",
            params={"Page": page, "PageSize": page_size},
        )
        return _as_list(data)

    async def get_points_of_sale(self) -> list[dict]:
        data = await self._get("/api/public/pointsOfSale")
        return _as_list(data)
