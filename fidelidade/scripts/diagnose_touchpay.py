"""Diagnóstico da API TouchPay REAL — SOMENTE LEITURA.

================================================================================
 Este script LÊ dados de PRODUÇÃO da TouchPay. NÃO modifica NADA.
 (Sem POST/PUT/DELETE, sem criar cupom, sem escrever.)
================================================================================

Rodar (a partir da pasta `fidelidade/`, com o venv):
    python -m scripts.diagnose_touchpay

Lê TOUCHPAY_BASE_URL e TOUCHPAY_TOKEN do .env. O token nunca é impresso. CPFs
são mascarados na saída (mostramos só "tem/não tem").
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.integrations.touchpay.http_client import (
    HttpTouchPayClient,
    TouchPayApiError,
)

_BANNER = (
    "=" * 70
    + "\n Este script LÊ dados de PRODUÇÃO da TouchPay. NÃO modifica nada.\n"
    + "=" * 70
)


def _g(d: dict, *keys: str) -> object:
    """Primeiro valor não-nulo entre as chaves candidatas (schema tolerante)."""
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


async def _points_of_sale(client: HttpTouchPayClient) -> None:
    print("\n--- Pontos de venda (pointsOfSale) ---")
    pos_list = await client.get_points_of_sale()
    if not pos_list:
        print("  (nenhum PDV retornado)")
        return
    for pos in pos_list:
        pid = _g(pos, "id", "pointOfSaleId", "Id")
        name = _g(pos, "name", "localName", "fantasyName", "tradeName", "Name")
        state = _g(pos, "state", "uf", "State", "stateAbbreviation")
        print(f"  id={pid} | {name} | {state}")
    print(f"  Total de PDVs: {len(pos_list)}")
    # Dump das chaves do primeiro PDV, para descobrir o schema real.
    print(f"  (campos disponíveis: {sorted(pos_list[0].keys())})")


async def _transactions(client: HttpTouchPayClient) -> None:
    print("\n--- Transações dos últimos 30 dias (página 1, pageSize 20) ---")
    max_date = datetime.now(timezone.utc)
    min_date = max_date - timedelta(days=30)
    paginated = await client.get_transactions(
        min_date=min_date, max_date=max_date, page=1, page_size=20
    )
    transactions = paginated.items or []
    with_cpf = 0
    for tx in transactions:
        has_cpf = bool(tx.userInfo and tx.userInfo.document)
        with_cpf += int(has_cpf)
        n_items = len(tx.items or [])
        print(
            f"  uuid={tx.uuid[:8]}... | CPF: {'sim' if has_cpf else 'não'} "
            f"| itens={n_items} | R$ {tx.totalPrice:.2f} | PDV={tx.pointOfSaleId}"
        )
    total = len(transactions)
    print(f"\n  >>> {with_cpf} de {total} transações têm CPF preenchido.")
    print(
        f"  (totalItems no período: {paginated.totalItems}, "
        f"páginas: {paginated.totalPages})"
    )


async def _products(client: HttpTouchPayClient) -> None:
    print("\n--- Produtos (primeira página) ---")
    products = await client.list_products(page=1, page_size=50)
    with_cat = 0
    for p in products[:10]:
        cat = _g(p, "categoryName", "category", "categoryDescription")
        with_cat += int(cat is not None)
        pid = _g(p, "productId", "id")
        desc = _g(p, "description", "name", "productName")
        print(f"  id={pid} | {desc} | categoria={cat}")
    # Conta categorias em toda a página, não só nos 10 impressos.
    total_cat = sum(
        1
        for p in products
        if _g(p, "categoryName", "category", "categoryDescription") is not None
    )
    print(
        f"\n  >>> {total_cat} de {len(products)} produtos têm categoria "
        f"preenchida (resto nulo)."
    )
    if products:
        print(f"  (campos disponíveis: {sorted(products[0].keys())})")


async def main() -> None:
    print(_BANNER)
    settings = get_settings()

    if not settings.touchpay_token:
        print("\nERRO: TOUCHPAY_TOKEN vazio no .env. Abortando.")
        return
    if (
        not settings.touchpay_base_url
        or "example.com" in settings.touchpay_base_url
    ):
        print(
            "\nERRO: TOUCHPAY_BASE_URL não configurada (ainda é o placeholder). "
            "Defina a URL real da API TouchPay no .env. Abortando."
        )
        return

    print(f"\nBase URL: {settings.touchpay_base_url}")
    print("Token: (carregado do .env — não exibido)")

    client = HttpTouchPayClient(
        base_url=settings.touchpay_base_url,
        token=settings.touchpay_token,
    )
    try:
        await _points_of_sale(client)
        await _transactions(client)
        await _products(client)
    except TouchPayApiError as exc:
        print(f"\nERRO na chamada à TouchPay: {exc}")
    finally:
        await client.aclose()

    print("\nConcluído. Nenhum dado foi modificado.")


if __name__ == "__main__":
    asyncio.run(main())
