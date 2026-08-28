"""Tradução de telefone Meta <-> formato canônico.

A Meta entrega o remetente em `messages[].from` como dígitos COM código de país
e sem "+": "5511987654321". O canônico da aplicação é sem o 55: "11987654321".

É o mesmo par de conversões da Evolution, e a mesma armadilha: errar aqui faz o
cliente "sumir" — o webhook procura pelo canônico e não acha o cadastro.
"""

from __future__ import annotations

from app.domain.phone import normalize_phone


def meta_to_canonical(raw: str) -> str:
    """"5511987654321" -> "11987654321". Idempotente."""
    return normalize_phone(raw)


def canonical_to_meta(phone: str) -> str:
    """"11987654321" -> "5511987654321" (formato de envio da Graph API)."""
    digits = normalize_phone(phone)
    if not digits:
        return ""
    return "55" + digits
