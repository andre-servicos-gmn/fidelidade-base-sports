"""Emissão e validação de JWT (HS256), com expiração configurável."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from app.config import get_settings

_ALGORITHM = "HS256"


def create_access_token(user_id: uuid.UUID | str, username: str) -> str:
    """Cria um access token assinado com o segredo da config."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": now,
        "exp": now + timedelta(hours=settings.jwt_expiration_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """Valida assinatura e expiração e retorna o payload.

    Levanta `jwt.PyJWTError` (subclasse de Exception) em token inválido/expirado.
    """
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[_ALGORITHM])
