"""Proteção do CPF: hash determinístico (HMAC) + máscara para exibição.

O CPF NUNCA é armazenado em texto plano. Guardamos:
  - `hash_cpf(cpf)`  -> HMAC-SHA256(pepper, dígitos_do_cpf) em hex.
    Determinístico: o mesmo CPF sempre gera o mesmo hash, o que permite
    buscar um cliente por CPF sem nunca persistir o CPF cru.
  - `mask_cpf(cpf)`  -> "123****89" (3 primeiros + 2 últimos), só para
    exibição/suporte.

A pepper é um segredo (lido da config). Sem ela, não dá para recomputar os
hashes — por isso ela nunca pode vazar nem ser commitada.
"""

from __future__ import annotations

import hashlib
import hmac
import re

from app.config import get_settings

_NON_DIGITS = re.compile(r"\D")


def _digits(cpf: str) -> str:
    """Normaliza o CPF para conter apenas dígitos."""
    return _NON_DIGITS.sub("", cpf or "")


def hash_cpf(cpf: str, pepper: str | None = None) -> str:
    """Retorna o HMAC-SHA256 (hex) do CPF normalizado.

    `pepper` é opcional; por padrão usa `settings.cpf_pepper`. O parâmetro
    existe sobretudo para facilitar testes determinísticos.
    """
    if pepper is None:
        pepper = get_settings().cpf_pepper
    digits = _digits(cpf)
    return hmac.new(
        pepper.encode("utf-8"),
        digits.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def is_valid_cpf(cpf: str) -> bool:
    """Valida o CPF pelos DÍGITOS VERIFICADORES, não só pelo tamanho.

    Por que importa: no autocadastro pelo WhatsApp o cliente digita o CPF, e um
    dígito errado criaria um cadastro fantasma — um cliente que nunca vai casar
    com nenhuma compra do totem. Checar o dígito elimina o erro de digitação.

    NÃO protege contra má-fé: um CPF de outra pessoa é matematicamente válido.
    Essa é uma preocupação separada (ver `register_customer_by_cpf`).

    Regras: 11 dígitos, não todos iguais, e os dois dígitos verificadores
    conferem pelo algoritmo oficial (módulo 11).
    """
    digits = _digits(cpf)
    if len(digits) != 11:
        return False
    # "11111111111" e afins passam no módulo 11 por acidente, mas não existem.
    if digits == digits[0] * 11:
        return False

    for size in (9, 10):
        weights = range(size + 1, 1, -1)
        total = sum(int(d) * w for d, w in zip(digits[:size], weights))
        remainder = total % 11
        expected = 0 if remainder < 2 else 11 - remainder
        if int(digits[size]) != expected:
            return False
    return True


def mask_cpf(cpf: str) -> str:
    """Retorna o CPF mascarado para exibição: "123****89".

    Mostra os 3 primeiros e os 2 últimos dígitos. Para entradas curtas
    demais para mascarar com segurança, devolve apenas asteriscos.
    """
    digits = _digits(cpf)
    if len(digits) < 5:
        return "*" * len(digits)
    return f"{digits[:3]}****{digits[-2:]}"


def mask_phone(phone: str | None) -> str | None:
    """Mascara o telefone para exibição, mostrando só os 4 últimos dígitos."""
    if not phone:
        return None
    digits = _NON_DIGITS.sub("", phone)
    if len(digits) < 4:
        return "*" * len(digits)
    return "*" * (len(digits) - 4) + digits[-4:]
