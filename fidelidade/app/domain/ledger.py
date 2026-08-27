"""Encadeamento de hash do ledger de pontos (append-only).

Cada lançamento carrega o hash do lançamento anterior daquele cliente,
formando uma cadeia estilo blockchain. Adulterar qualquer lançamento muda o
seu `entry_hash`, o que quebra o `previous_hash` de todos os seguintes — e a
verificação falha.

ORDEM CANÔNICA DA CONCATENAÇÃO (não mude sem migrar os hashes existentes):

    customer_id | sequence | entry_type | points | balance_after |
    previous_hash | created_at(ISO-8601)

Campos unidos por "|" (pipe). `previous_hash` nulo vira string vazia.
`entry_type` é serializado pelo seu valor (ex: "EARN"). `created_at` usa
`datetime.isoformat()`. O resultado é codificado em UTF-8 e passado por
SHA-256, retornado em hex.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Protocol
from uuid import UUID

_SEPARATOR = "|"


def _enum_value(value: object) -> str:
    """Serializa um enum pelo seu `.value`; demais valores via `str()`."""
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def compute_entry_hash(
    customer_id: UUID | str,
    sequence: int,
    entry_type: object,
    points: int,
    balance_after: int,
    previous_hash: str | None,
    created_at: datetime,
) -> str:
    """Calcula o SHA-256 (hex) determinístico de um lançamento.

    Veja a ordem canônica de concatenação no docstring do módulo.
    """
    canonical = _SEPARATOR.join(
        [
            str(customer_id),
            str(sequence),
            _enum_value(entry_type),
            str(points),
            str(balance_after),
            previous_hash or "",
            created_at.isoformat(),
        ]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class _LedgerEntryLike(Protocol):
    """Forma mínima esperada de um lançamento (modelo ou dataclass)."""

    customer_id: UUID | str
    sequence: int
    entry_type: object
    points: int
    balance_after: int
    previous_hash: str | None
    entry_hash: str
    created_at: datetime


def verify_chain(entries: list[_LedgerEntryLike]) -> bool:
    """Verifica a integridade da cadeia de um cliente.

    `entries` deve estar ordenado por `sequence`. Para cada lançamento:
      1. `previous_hash` precisa bater com o `entry_hash` do anterior
         (ou ser nulo/vazio no primeiro);
      2. o `entry_hash` armazenado precisa bater com o recomputado a partir
         dos campos atuais.

    Retorna False se a cadeia foi adulterada em qualquer ponto.
    """
    expected_previous: str | None = None
    for entry in entries:
        # 1) Encadeamento: o previous_hash precisa apontar para o anterior.
        if (entry.previous_hash or None) != (expected_previous or None):
            return False

        # 2) Integridade: o hash armazenado precisa bater com o recomputado.
        recomputed = compute_entry_hash(
            entry.customer_id,
            entry.sequence,
            entry.entry_type,
            entry.points,
            entry.balance_after,
            entry.previous_hash,
            entry.created_at,
        )
        if recomputed != entry.entry_hash:
            return False

        expected_previous = entry.entry_hash

    return True
