"""Hashing de senha com bcrypt. Senha NUNCA é armazenada em texto plano."""

from __future__ import annotations

import bcrypt

# bcrypt trunca em 72 bytes; cortamos explicitamente para evitar erro/surpresa.
_MAX_BYTES = 72


def _encode(plain: str) -> bytes:
    return plain.encode("utf-8")[:_MAX_BYTES]


def hash_password(plain: str) -> str:
    """Gera o hash bcrypt (com salt) da senha, em string para persistir."""
    return bcrypt.hashpw(_encode(plain), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verifica a senha contra o hash. Nunca levanta — retorna False se inválido."""
    try:
        return bcrypt.checkpw(_encode(plain), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False
