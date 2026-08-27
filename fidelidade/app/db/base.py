"""Base declarativa, engine async e session factory.

O engine e a session factory são criados de forma preguiçosa (lazy) e
cacheados. Assim, importar este módulo (por exemplo nos testes unitários de
domínio) não tenta abrir conexão com o banco — a conexão só acontece quando
alguém realmente pede uma sessão.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    """Base declarativa de todos os modelos."""


@lru_cache
def get_engine() -> AsyncEngine:
    """Cria (uma vez) o engine async a partir da `database_url`."""
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError(
            "DATABASE_URL não configurada. Defina-a no .env "
            "(formato postgresql+asyncpg://...)."
        )
    return create_async_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Session factory async, cacheada."""
    return async_sessionmaker(
        get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )
