"""Testes do encadeamento de hash do ledger (puros, sem banco).

Usamos os próprios modelos SQLAlchemy SEM persistir — instâncias em memória
bastam, já que `verify_chain`/`compute_entry_hash` só leem atributos.
"""

import uuid
from datetime import datetime, timezone

from app.db.models import LedgerEntry, LedgerEntryType
from app.domain.ledger import compute_entry_hash, verify_chain


def _build_valid_chain() -> list[LedgerEntry]:
    """Constrói uma cadeia válida de 3 lançamentos para um cliente."""
    customer_id = uuid.uuid4()
    steps = [
        (LedgerEntryType.EARN, 100),
        (LedgerEntryType.REDEEM, -30),
        (LedgerEntryType.EARN, 50),
    ]

    entries: list[LedgerEntry] = []
    previous_hash: str | None = None
    balance = 0

    for i, (entry_type, points) in enumerate(steps, start=1):
        balance += points
        created_at = datetime(2026, 1, i, 12, 0, 0, tzinfo=timezone.utc)
        entry_hash = compute_entry_hash(
            customer_id,
            i,
            entry_type,
            points,
            balance,
            previous_hash,
            created_at,
        )
        entries.append(
            LedgerEntry(
                id=uuid.uuid4(),
                customer_id=customer_id,
                sequence=i,
                entry_type=entry_type,
                points=points,
                balance_after=balance,
                previous_hash=previous_hash,
                entry_hash=entry_hash,
                created_at=created_at,
            )
        )
        previous_hash = entry_hash

    return entries


def test_valid_chain_verifies():
    entries = _build_valid_chain()
    assert verify_chain(entries) is True


def test_tampered_middle_entry_breaks_chain():
    entries = _build_valid_chain()

    # Adultera o `points` do lançamento do meio (sequence 2) sem recomputar
    # o hash — é exatamente o que um atacante faria editando o banco.
    entries[1].points = 999

    assert verify_chain(entries) is False


def test_tampered_hash_link_breaks_chain():
    entries = _build_valid_chain()

    # Quebra o encadeamento: aponta o previous_hash do 3º para um valor falso.
    entries[2].previous_hash = "deadbeef"

    assert verify_chain(entries) is False


def test_single_entry_chain_verifies():
    entries = _build_valid_chain()[:1]
    assert verify_chain(entries) is True
