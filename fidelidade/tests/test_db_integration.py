"""Testes que exigem um Supabase/PostgreSQL real.

Todos marcados com @pytest.mark.integration — são pulados na coleta padrão.
Para rodar:  pytest -m integration
Exige DATABASE_URL configurada no .env e conectividade com o banco.

Cada teste cria e descarta seu PRÓPRIO engine (com NullPool). Não usamos o
engine global cacheado (`app.db.base.get_engine`) de propósito: o pytest-asyncio
cria um event loop novo por teste, e um pool reaproveitado entre loops quebra
com "Event loop is closed".
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings


@pytest_asyncio.fixture
async def conn() -> AsyncIterator[AsyncConnection]:
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL não configurada.")

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            yield connection
    finally:
        await engine.dispose()


@pytest.mark.integration
async def test_database_connection(conn: AsyncConnection):
    result = await conn.execute(text("SELECT 1"))
    assert result.scalar() == 1


@pytest.mark.integration
async def test_tables_exist_after_migration(conn: AsyncConnection):
    """Verifica que `alembic upgrade head` já criou as 3 tabelas."""
    expected = {"customers", "ledger_entries", "coupon_pool"}
    result = await conn.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public'"
        )
    )
    tables = {row[0] for row in result}
    assert expected.issubset(tables)
