"""Demo ponta a ponta: simula compras (TouchPay mock) e credita pontos.

Roda o MESMO caminho de produção: lê transações do TouchPay (aqui, o mock com 4
compras fictícias) -> motor de pontuação -> ledger (append-only, hash-encadeado).
Depois imprime o relatório e o saldo de cada cliente.

Pré-requisito: existir uma regra BASE ativa (1 ponto/real). O script cria uma se
não houver. Os pontos caem no MESMO banco que o painel lê, então o saldo aparece
na aba Clientes (busque pelo CPF).

Uso (a partir de `fidelidade/`, com o venv):
    python -m scripts.simulate_purchase

Idempotente: a ingestão não credita a mesma compra duas vezes (constraint única
em source_reference), então pode rodar de novo sem inflar saldo.

AVISO: cria dados de TESTE (CPFs 111..., 222..., etc.). Remova antes do go-live.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.base import get_engine, get_sessionmaker
from app.db.models import Customer, LedgerEntry, ScoringRule
from app.domain.scoring import RuleType
from app.domain.security import hash_cpf
from app.integrations.touchpay.mock_client import MockTouchPayClient
from app.services.ingestion_service import ingest_transactions

MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
MAX_DATE = datetime(2026, 12, 31, tzinfo=timezone.utc)
MOCK_DOCS = ["11111111111", "22222222222", "33333333333", "44444444444"]


async def ensure_base_rule(session) -> None:
    existing = (
        await session.execute(
            select(ScoringRule).where(
                ScoringRule.rule_type == RuleType.BASE,
                ScoringRule.active.is_(True),
            )
        )
    ).scalars().first()
    if existing is not None:
        print(f"Regra BASE já existe: '{existing.name}'.")
        return
    session.add(
        ScoringRule(
            id=uuid.uuid4(),
            name="DEMO Base (1 ponto/real)",
            rule_type=RuleType.BASE,
            priority=100,
            active=True,
            params={"points_per_real": 1.0},
        )
    )
    await session.commit()
    print("Regra BASE criada: 'DEMO Base (1 ponto/real)'.")


async def main() -> None:
    maker = get_sessionmaker()
    async with maker() as session:
        await ensure_base_rule(session)

        print("\n>>> Simulando ingestão das compras (TouchPay mock)...")
        report = await ingest_transactions(
            session, MockTouchPayClient(), MIN_DATE, MAX_DATE
        )
        print(
            f"  lidas={report.total_read} creditadas={report.credited} "
            f"já_processadas={report.already_processed} "
            f"sem_cpf={report.skipped_no_cpf} erros={report.errors}"
        )
        print(f"  total de pontos creditados: {report.total_points_credited}")
        print(
            f"  prompts de afiliado gerados: {len(report.affiliate_prompts)} "
            "(perguntas pós-compra para clientes com telefone)"
        )

        print("\n>>> Saldos por cliente (CPF -> pontos):")
        for cpf in MOCK_DOCS:
            customer = (
                await session.execute(
                    select(Customer).where(Customer.cpf_hash == hash_cpf(cpf))
                )
            ).scalar_one_or_none()
            if customer is None:
                print(f"  {cpf}: (sem cliente)")
                continue
            bal = (
                await session.execute(
                    select(LedgerEntry.balance_after)
                    .where(LedgerEntry.customer_id == customer.id)
                    .order_by(LedgerEntry.sequence.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            print(f"  {cpf}: {bal or 0} pontos  (id={customer.id})")

    await get_engine().dispose()
    print("\nPronto. Abra o painel -> Clientes e busque um CPF para ver cair.")


if __name__ == "__main__":
    asyncio.run(main())
