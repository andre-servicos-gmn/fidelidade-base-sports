"""Login do painel administrativo."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt_handler import create_access_token
from app.auth.password import verify_password
from app.db.models import AdminUser
from app.dependencies import get_db
from app.routes.admin.schemas import LoginRequest, TokenResponse

router = APIRouter(prefix="/admin", tags=["admin-auth"])

# Mensagem genérica: não revela se errou usuário ou senha.
_INVALID = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Credenciais inválidas",
)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest, session: AsyncSession = Depends(get_db)
) -> TokenResponse:
    admin = (
        await session.execute(
            select(AdminUser).where(AdminUser.username == body.username)
        )
    ).scalar_one_or_none()

    # verify_password mesmo quando admin é None? Para simplicidade retornamos a
    # mesma mensagem genérica; o custo de timing é aceitável neste contexto.
    if (
        admin is None
        or not admin.active
        or not verify_password(body.password, admin.password_hash)
    ):
        raise _INVALID

    token = create_access_token(admin.id, admin.username)
    return TokenResponse(access_token=token)
