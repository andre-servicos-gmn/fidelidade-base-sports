"""Testes do RedisSessionStore (Fase 3).

- Unit (sem Redis real): round-trip de serialização e política fail-safe, com
  um cliente Redis fake. Cobrem o passo mais arriscado sem exigir infra.
- Integração (`@pytest.mark.integration`, exige Redis): set/get/delete/TTL
  contra um Redis real. Pula se REDIS_URL não estiver configurada, no mesmo
  padrão dos testes de banco.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
import pytest_asyncio

from app.config import get_settings
from app.whatsapp.session_store import (
    ConversationState,
    ConversationStep,
    RedisSessionStore,
    _deserialize,
    _serialize,
)


# --------------------------------------------------------------------------- #
# Fakes                                                                        #
# --------------------------------------------------------------------------- #
class _FakeRedis:
    """Redis async em memória, só o suficiente para o store (get/set/delete)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.last_ex: int | None = None

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None):
        self.store[key] = value
        self.last_ex = ex

    async def delete(self, key: str):
        self.store.pop(key, None)


class _BoomRedis:
    """Cliente que sempre falha — para exercitar a política fail-safe."""

    async def get(self, key: str):
        raise ConnectionError("redis down")

    async def set(self, key: str, value: str, ex: int | None = None):
        raise ConnectionError("redis down")

    async def delete(self, key: str):
        raise ConnectionError("redis down")


# --------------------------------------------------------------------------- #
# Serialização (round-trip)                                                    #
# --------------------------------------------------------------------------- #
def test_serialize_roundtrip_reward_state():
    """Estado de resgate (lista de rewards) sobrevive a dumps -> loads igual."""
    rewards = [
        {
            "reward_id": "FIXED|10.00|500",
            "discount_type": "FIXED",
            "discount_value": 10.0,
            "points_cost": 500,
            "available_count": 3,
            "min_order_value": 50.0,
        },
        {
            "reward_id": "PERCENTAGE|15|1000",
            "discount_type": "PERCENTAGE",
            "discount_value": 15.0,
            "points_cost": 1000,
            "available_count": 1,
            "min_order_value": None,
        },
    ]
    state = ConversationState(
        step=ConversationStep.AWAITING_REWARD_CHOICE, data={"rewards": rewards}
    )

    restored = _deserialize(_serialize(state))

    assert restored.step is ConversationStep.AWAITING_REWARD_CHOICE
    assert restored.data == {"rewards": rewards}


def test_serialize_roundtrip_affiliate_state():
    """Estado de afiliado (amount já como str) sobrevive intacto."""
    state = ConversationState(
        step=ConversationStep.AWAITING_AFFILIATE_CODE,
        data={
            "source_reference": "tx-1",
            "points": 350,
            "amount": str(Decimal("350.0")),
        },
    )

    restored = _deserialize(_serialize(state))

    assert restored.step is ConversationStep.AWAITING_AFFILIATE_CODE
    assert restored.data["source_reference"] == "tx-1"
    assert restored.data["points"] == 350
    assert restored.data["amount"] == "350.0"


# --------------------------------------------------------------------------- #
# Store com fake (sem Redis real)                                             #
# --------------------------------------------------------------------------- #
async def test_store_set_get_delete_with_fake():
    client = _FakeRedis()
    store = RedisSessionStore(client, ttl_seconds=600)

    await store.set(
        "11990000001", ConversationState(step=ConversationStep.AWAITING_CPF)
    )
    # A chave é namespaced e o TTL é aplicado.
    assert "fidelidade:session:11990000001" in client.store
    assert client.last_ex == 600

    state = await store.get("11990000001")
    assert state is not None and state.step is ConversationStep.AWAITING_CPF

    await store.delete("11990000001")
    assert await store.get("11990000001") is None


async def test_get_missing_returns_none():
    store = RedisSessionStore(_FakeRedis())
    assert await store.get("inexistente") is None


# --------------------------------------------------------------------------- #
# Fail-safe                                                                    #
# --------------------------------------------------------------------------- #
async def test_failsafe_get_returns_none_when_redis_down():
    store = RedisSessionStore(_BoomRedis())
    # NÃO propaga: retorna None (conversa cai no menu).
    assert await store.get("11990000001") is None


async def test_failsafe_set_and_delete_swallow_errors():
    store = RedisSessionStore(_BoomRedis())
    # NÃO propaga: o webhook não pode virar 500 por causa do Redis.
    await store.set(
        "11990000001", ConversationState(step=ConversationStep.AWAITING_CPF)
    )
    await store.delete("11990000001")


async def test_corrupted_value_is_ignored():
    client = _FakeRedis()
    client.store["fidelidade:session:x"] = "{not valid json"
    store = RedisSessionStore(client)
    assert await store.get("x") is None


# --------------------------------------------------------------------------- #
# Factory por config                                                          #
# --------------------------------------------------------------------------- #
def test_factory_returns_inmemory_by_default(monkeypatch):
    from app import dependencies
    from app.whatsapp.session_store import InMemorySessionStore

    get_settings.cache_clear()
    dependencies.get_session_store.cache_clear()
    monkeypatch.setenv("USE_REDIS_SESSION_STORE", "false")
    get_settings.cache_clear()

    store = dependencies.get_session_store()
    assert isinstance(store, InMemorySessionStore)

    dependencies.get_session_store.cache_clear()
    get_settings.cache_clear()


# --------------------------------------------------------------------------- #
# Integração (Redis real)                                                     #
# --------------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def redis_store() -> AsyncIterator[RedisSessionStore]:
    settings = get_settings()
    if not settings.redis_url:
        pytest.skip("REDIS_URL não configurada.")
    from redis.asyncio import Redis

    client = Redis.from_url(settings.redis_url, decode_responses=True)
    store = RedisSessionStore(client, ttl_seconds=2)
    try:
        yield store
    finally:
        await client.delete("fidelidade:session:int-test")
        await client.aclose()


@pytest.mark.integration
async def test_redis_roundtrip_real(redis_store: RedisSessionStore):
    await redis_store.set(
        "int-test",
        ConversationState(
            step=ConversationStep.AWAITING_AFFILIATE_CODE,
            data={"source_reference": "tx-9", "points": 10, "amount": "10.0"},
        ),
    )
    state = await redis_store.get("int-test")
    assert state is not None
    assert state.step is ConversationStep.AWAITING_AFFILIATE_CODE
    assert state.data["source_reference"] == "tx-9"

    await redis_store.delete("int-test")
    assert await redis_store.get("int-test") is None


@pytest.mark.integration
async def test_redis_ttl_expires_real(redis_store: RedisSessionStore):
    import asyncio

    await redis_store.set(
        "int-test", ConversationState(step=ConversationStep.AWAITING_CPF)
    )
    assert await redis_store.get("int-test") is not None
    # TTL do fixture é 2s; espera expirar.
    await asyncio.sleep(2.5)
    assert await redis_store.get("int-test") is None
