"""Formato canônico de telefone — módulo de domínio PURO (sem banco, sem I/O).

Mora aqui, e não em `identity_service`, para quebrar um ciclo de import: tanto
`customer_service` (ao gravar o telefone) quanto `identity_service` (ao buscar
por telefone) precisam desta função, e um importa o outro.

Definição do canônico: apenas dígitos, com DDD, SEM o código de país.
Ex.: "+55 (11) 99000-0001" e "5511990000001" -> "11990000001".

Errar isto faz o cliente "sumir" para o bot: o webhook procura pelo canônico e
não encontra o cadastro. Por isso existe uma definição única, num só lugar.
"""

from __future__ import annotations

import re

_NON_DIGITS = re.compile(r"\D")


def normalize_phone(phone: str) -> str:
    """Normaliza o telefone para o formato canônico (só dígitos, com DDD)."""
    digits = _NON_DIGITS.sub("", phone or "")
    # Remove o código de país do Brasil (55) quando vier no começo.
    if len(digits) >= 12 and digits.startswith("55"):
        digits = digits[2:]
    return digits
