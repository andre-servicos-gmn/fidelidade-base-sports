"""Cria o usuário admin inicial do painel. IDEMPOTENTE.

Rodar (a partir de `fidelidade/`, com o venv):
    python -m scripts.seed_admin

Senha: NUNCA hardcoded. Lida de INITIAL_ADMIN_PASSWORD (env). Se não houver, o
script GERA uma senha aleatória forte e a imprime UMA vez (guarde-a). O usuário
vem de INITIAL_ADMIN_USERNAME (default "admin").

Idempotente: se o usuário já existir, não recria nem altera a senha.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.password import hash_password
from app.db.base import get_engine, get_sessionmaker
from app.db.models import AdminUser


async def seed_admin(
    session: AsyncSession, username: str, password: str, name: str = "Administrador"
) -> dict:
    """Cria o admin se não existir. Retorna {'status': 'criado'|'já existia'}."""
    existing = (
        await session.execute(
            select(AdminUser).where(AdminUser.username == username)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return {"status": "já existia", "username": username}

    session.add(
        AdminUser(
            id=uuid.uuid4(),
            username=username,
            password_hash=hash_password(password),
            name=name,
            active=True,
        )
    )
    await session.commit()
    return {"status": "criado", "username": username}


async def main() -> None:
    print("=== Seed do admin inicial (idempotente) ===")
    username = os.getenv("INITIAL_ADMIN_USERNAME", "admin")
    password = os.getenv("INITIAL_ADMIN_PASSWORD")
    generated = False
    if not password:
        password = secrets.token_urlsafe(16)
        generated = True

    maker = get_sessionmaker()
    async with maker() as session:
        result = await seed_admin(session, username, password)

    if result["status"] == "criado":
        print(f"Admin criado: username='{username}'")
        if generated:
            print("\n" + "=" * 60)
            print(f" SENHA GERADA (guarde agora — não será exibida de novo):")
            print(f"   {password}")
            print("=" * 60)
        else:
            print("Senha definida via INITIAL_ADMIN_PASSWORD.")
    else:
        print(f"Admin '{username}' já existia. Nada alterado.")

    await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
