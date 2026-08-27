"""Cria um CLIENTE DE TESTE com saldo inicial. IDEMPOTENTE.

Rodar (a partir de `fidelidade/`, com o venv):
    python -m scripts.seed_test_customer

Cria/garante um cliente de teste (CPF 000.000.000-01, telefone de teste) e
credita 1500 pontos via um lançamento ADJUST no ledger — para testar resgate
sem depender da ingestão.

================================ AVISO ========================================
 Dado de TESTE. O CPF 00000000001 e o telefone 11900000000 não são reais.
 Remover antes do go-live.
==============================================================================
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_engine, get_sessionmaker
from app.db.models import LedgerEntryType
from app.services.customer_service import get_or_create_customer
from app.services.identity_service import link_phone_to_cpf
from app.services.ledger_service import add_entry, get_balance

TEST_CPF = "00000000001"
TEST_PHONE = "11900000000"
SEED_POINTS = 1500
# Chave natural do crédito de seed: a constraint única de source_reference torna
# o ADJUST idempotente (rodar de novo não credita 1500 outra vez).
SEED_SOURCE_REF = f"SEED-ADJUST-{TEST_CPF}"


async def seed_test_customer(session: AsyncSession) -> dict:
    """Executa o seed do cliente de teste e retorna um resumo."""
    customer = await get_or_create_customer(session, TEST_CPF)
    await session.commit()
    customer_id = customer.id

    await link_phone_to_cpf(session, TEST_PHONE, TEST_CPF)

    entry = await add_entry(
        session,
        customer_id=customer_id,
        entry_type=LedgerEntryType.ADJUST,
        points=SEED_POINTS,
        source_reference=SEED_SOURCE_REF,
        description="Seed de teste: saldo inicial",
    )
    await session.commit()

    balance = await get_balance(session, customer_id)
    return {
        "customer_id": str(customer_id),
        "credited_now": entry is not None,
        "balance": balance,
    }


async def main() -> None:
    print("=== Seed de CLIENTE DE TESTE (idempotente) ===")
    print(f"AVISO: dado de teste. CPF {TEST_CPF}, telefone {TEST_PHONE}.\n")

    maker = get_sessionmaker()
    async with maker() as session:
        summary = await seed_test_customer(session)

    print(f"Cliente de teste id={summary['customer_id']}")
    if summary["credited_now"]:
        print(f"Creditado {SEED_POINTS} pontos (ADJUST).")
    else:
        print("Saldo de seed já havia sido creditado (idempotente).")
    print(f"Saldo atual: {summary['balance']} pontos.")
    await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
