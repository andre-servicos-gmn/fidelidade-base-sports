"""Dependency de autenticação admin: valida o Bearer JWT e carrega o AdminUser.

Usa `HTTPBearer` (com `auto_error=False`) para que:
  - o Swagger mostre o botão "Authorize" com campo de Bearer token;
  - a AUSÊNCIA de token retorne 401 (e não o 403 padrão do HTTPBearer).
"""

from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.jwt_handler import decode_token
from app.db.models import AdminUser
from app.dependencies import get_db
from sqlalchemy.ext.asyncio import AsyncSession

_bearer = HTTPBearer(auto_error=False, description="Bearer JWT obtido no /admin/login")

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Não autenticado",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_db),
) -> AdminUser:
    """Retorna o AdminUser autenticado ou levanta 401."""
    if credentials is None or (credentials.scheme or "").lower() != "bearer":
        raise _UNAUTHORIZED

    try:
        payload = decode_token(credentials.credentials)
        admin_id = uuid.UUID(str(payload.get("sub")))
    except Exception:  # token inválido/expirado/sub malformado
        raise _UNAUTHORIZED

    admin = await session.get(AdminUser, admin_id)
    if admin is None or not admin.active:
        raise _UNAUTHORIZED
    return admin
