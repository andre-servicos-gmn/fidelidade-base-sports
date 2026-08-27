"""Serviço do ledger: append de lançamentos com hash chaining, transacional.

O ledger é APPEND-ONLY. Cada lançamento encadeia o hash do anterior do mesmo
cliente (ver `app.domain.ledger`).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Customer, LedgerEntry, LedgerEntryType
from app.domain.ledger import compute_entry_hash


async def add_entry(
    session: AsyncSession,
    customer_id: uuid.UUID,
    entry_type: LedgerEntryType,
    points: int,
    source_reference: str | None = None,
    description: str | None = None,
) -> LedgerEntry | None:
    """Adiciona um lançamento ao ledger do cliente. Retorna None se duplicado.

    Estratégia de concorrência
    --------------------------
    Antes de calcular `sequence`/`balance_after`, travamos a LINHA do cliente
    com `SELECT ... FOR UPDATE`. Isso serializa appends concorrentes do MESMO
    cliente (clientes diferentes não se bloqueiam), evitando duas linhas com o
    mesmo `sequence` ou um `balance_after` calculado sobre saldo desatualizado.
    O lock é liberado no commit (feito pelo chamador).

    Idempotência
    ------------
    Quando `source_reference` é não-nulo, a constraint única
    `uq_ledger_source_reference` garante que a mesma transação TouchPay só vire
    UM lançamento. O INSERT é feito dentro de um SAVEPOINT (`begin_nested`); se
    a constraint disparar (transação já processada), revertemos apenas o
    savepoint e retornamos **None** — não é erro, é a idempotência funcionando.
    A transação externa permanece intacta (não aborta o lote).

    `created_at` é gerado aqui (UTC) e gravado explicitamente, em vez de usar o
    default do banco, porque ele entra no cálculo do `entry_hash` e precisa ser
    conhecido ANTES do INSERT (e idêntico ao que fica persistido).
    """
    # 1) Trava a linha do cliente para serializar appends concorrentes.
    await session.execute(
        select(Customer.id).where(Customer.id == customer_id).with_for_update()
    )

    # 2) Último lançamento do cliente (maior sequence).
    last_result = await session.execute(
        select(LedgerEntry)
        .where(LedgerEntry.customer_id == customer_id)
        .order_by(LedgerEntry.sequence.desc())
        .limit(1)
    )
    last = last_result.scalar_one_or_none()

    if last is None:
        sequence = 1
        previous_balance = 0
        previous_hash: str | None = None
    else:
        sequence = last.sequence + 1
        previous_balance = last.balance_after
        previous_hash = last.entry_hash

    balance_after = previous_balance + points
    created_at = datetime.now(timezone.utc)
    entry_hash = compute_entry_hash(
        customer_id=customer_id,
        sequence=sequence,
        entry_type=entry_type,
        points=points,
        balance_after=balance_after,
        previous_hash=previous_hash,
        created_at=created_at,
    )

    entry = LedgerEntry(
        id=uuid.uuid4(),
        customer_id=customer_id,
        sequence=sequence,
        entry_type=entry_type,
        points=points,
        balance_after=balance_after,
        source_reference=source_reference,
        description=description,
        previous_hash=previous_hash,
        entry_hash=entry_hash,
        created_at=created_at,
    )

    # 3) INSERT isolado num savepoint para capturar a duplicidade sem abortar
    #    a transação externa (o lote inteiro).
    try:
        async with session.begin_nested():
            session.add(entry)
            await session.flush()
    except IntegrityError:
        return None

    return entry


async def is_first_purchase(
    session: AsyncSession,
    customer_id: uuid.UUID,
    source_reference: str,
) -> bool:
    """True se `source_reference` for a PRIMEIRA compra creditada do cliente.

    "Compra" aqui é lançamento `EARN` COM `source_reference` — ou seja, veio da
    TouchPay. Ajustes manuais, resgates e expirações não contam, mesmo estando
    no mesmo ledger.

    Usada pela trava de comissão do afiliado: ele é remunerado por TRAZER o
    cliente, então só a primeira compra daquele CPF gera pontos para ele.

    Lê a fonte de verdade (o ledger) em vez de confiar em algo carregado no
    estado da conversa: o estado é efêmero e vem do cliente, o ledger não.
    """
    first = (
        await session.execute(
            select(LedgerEntry.source_reference)
            .where(
                LedgerEntry.customer_id == customer_id,
                LedgerEntry.entry_type == LedgerEntryType.EARN,
                LedgerEntry.source_reference.is_not(None),
            )
            .order_by(LedgerEntry.sequence)
            .limit(1)
        )
    ).scalar_one_or_none()
    return first is not None and first == source_reference


async def get_balance(session: AsyncSession, customer_id: uuid.UUID) -> int:
    """Saldo atual = `balance_after` do último lançamento, ou 0 se não houver."""
    result = await session.execute(
        select(LedgerEntry.balance_after)
        .where(LedgerEntry.customer_id == customer_id)
        .order_by(LedgerEntry.sequence.desc())
        .limit(1)
    )
    balance = result.scalar_one_or_none()
    return balance if balance is not None else 0
