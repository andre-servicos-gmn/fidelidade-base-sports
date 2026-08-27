"""Testes da API de afiliados (/admin/affiliates).

Integração: usam o banco real via ASGITransport. Cobrem auth, criação válida,
código inválido (422), código duplicado case-insensitive (409), listagem,
atualização, toggle e delete.
"""

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.auth.password import hash_password
from app.config import get_settings
from app.db.models import AdminUser, Affiliate
from app.dependencies import get_session_factory
from app.main import app

pytestmark = pytest.mark.integration

ADMIN_USER = "test-affiliate-admin"
ADMIN_PASS = "s3nh4-forte-teste"
CODE_PREFIX = "AFFTEST-"


async def _purge(session: AsyncSession) -> None:
    await session.execute(
        delete(Affiliate).where(Affiliate.code.like(f"{CODE_PREFIX}%"))
    )
    await session.execute(
        delete(AdminUser).where(AdminUser.username == ADMIN_USER)
    )
    await session.commit()


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL não configurada.")
    eng = create_async_engine(settings.database_url, poolclass=NullPool)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    async with maker() as s:
        await _purge(s)
        s.add(
            AdminUser(
                id=uuid.uuid4(),
                username=ADMIN_USER,
                password_hash=hash_password(ADMIN_PASS),
                name="Teste",
                active=True,
            )
        )
        await s.commit()
    try:
        yield eng
    finally:
        async with maker() as s:
            await _purge(s)
        await eng.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _wire_app(engine: AsyncEngine) -> AsyncIterator[None]:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    app.dependency_overrides[get_session_factory] = lambda: maker
    yield
    app.dependency_overrides.clear()


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _auth(client: AsyncClient) -> dict:
    resp = await client.post(
        "/admin/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _payload(**over) -> dict:
    base = {
        "name": "Prof. João",
        "affiliate_type": "PROFESSOR",
        "code": f"{CODE_PREFIX}001",
        "points_rate": 100,
        "contact": "11999990001",
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------- #
async def test_affiliates_require_auth():
    async with _client() as client:
        resp = await client.get("/admin/affiliates")
        assert resp.status_code == 401


async def test_create_affiliate_normalizes_code():
    async with _client() as client:
        headers = await _auth(client)
        # Código em minúsculas deve ser normalizado para MAIÚSCULAS.
        ok = await client.post(
            "/admin/affiliates",
            headers=headers,
            json=_payload(code=f"{CODE_PREFIX.lower()}001"),
        )
        assert ok.status_code == 201
        body = ok.json()
        assert body["code"] == f"{CODE_PREFIX}001"
        assert body["affiliate_type"] == "PROFESSOR"
        assert body["active"] is True
        assert float(body["points_rate"]) == 100.0


async def test_create_affiliate_invalid_code():
    async with _client() as client:
        headers = await _auth(client)
        bad = await client.post(
            "/admin/affiliates", headers=headers, json=_payload(code="a b!")
        )
        assert bad.status_code == 422


async def test_create_affiliate_negative_rate():
    async with _client() as client:
        headers = await _auth(client)
        bad = await client.post(
            "/admin/affiliates", headers=headers, json=_payload(points_rate=-5)
        )
        assert bad.status_code == 422


async def test_create_affiliate_duplicate_code_conflict():
    async with _client() as client:
        headers = await _auth(client)
        first = await client.post(
            "/admin/affiliates", headers=headers, json=_payload()
        )
        assert first.status_code == 201
        # Mesmo código em minúsculas -> deve colidir (case-insensitive) -> 409.
        dup = await client.post(
            "/admin/affiliates",
            headers=headers,
            json=_payload(name="Outro", code=f"{CODE_PREFIX.lower()}001"),
        )
        assert dup.status_code == 409


async def test_list_update_toggle_delete():
    async with _client() as client:
        headers = await _auth(client)
        created = await client.post(
            "/admin/affiliates",
            headers=headers,
            json=_payload(code=f"{CODE_PREFIX}050", affiliate_type="INFLUENCER"),
        )
        aff_id = created.json()["id"]

        listing = await client.get("/admin/affiliates", headers=headers)
        assert listing.status_code == 200
        ours = [
            a for a in listing.json() if a["code"].startswith(CODE_PREFIX)
        ]
        assert any(a["id"] == aff_id for a in ours)

        # Update: troca nome e tipo.
        updated = await client.put(
            f"/admin/affiliates/{aff_id}",
            headers=headers,
            json=_payload(
                name="Influencer X",
                code=f"{CODE_PREFIX}050",
                affiliate_type="INFLUENCER",
            ),
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Influencer X"

        # Toggle: ativa -> inativa.
        toggled = await client.patch(
            f"/admin/affiliates/{aff_id}/toggle", headers=headers
        )
        assert toggled.status_code == 200
        assert toggled.json()["active"] is False

        # Delete.
        deleted = await client.delete(
            f"/admin/affiliates/{aff_id}", headers=headers
        )
        assert deleted.status_code == 204

        gone = await client.get(
            f"/admin/affiliates/{aff_id}", headers=headers
        )
        assert gone.status_code == 404
