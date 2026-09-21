"""Resposta à pergunta de indicação, casada pelo BANCO e não pela sessão.

Testes PUROS: sem banco nem rede. `handle_message` roda de verdade, com um
`InMemorySessionStore` real; só as funções de serviço importadas em
`app.whatsapp.conversation` são trocadas por fakes em memória (monkeypatch).

O ponto central: a pergunta pendente vem de `get_pending_affiliate_question`,
então um "Sim" que chega horas depois — sem estado de conversa nenhum — ainda
é reconhecido. A versão integrada (Postgres real) está em
`test_conversation.py`.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.db.models import AffiliateQuestionStatus
from app.whatsapp import conversation, messages
from app.whatsapp.conversation import handle_message
from app.whatsapp.session_store import (
    ConversationState,
    ConversationStep,
    InMemorySessionStore,
)

PHONE = "11987654321"
CUSTOMER_ID = uuid.uuid4()
AFF_CODE = "PROF-ANA"
BALANCE = 777


@dataclass
class FakeSession:
    """Sessão que só conta commits (os serviços que usariam o banco são fakes)."""

    commits: int = 0

    async def commit(self) -> None:
        self.commits += 1


@dataclass
class World:
    """Estado do "banco" fake e registro das chamadas."""

    session: FakeSession = field(default_factory=FakeSession)
    question: SimpleNamespace | None = None
    attributions: list[dict] = field(default_factory=list)
    first_purchase: bool = True
    window_days_seen: list[int] = field(default_factory=list)


def _question(**overrides) -> SimpleNamespace:
    data = dict(
        id=uuid.uuid4(),
        customer_id=CUSTOMER_ID,
        source_reference="TX-42",
        points=120,
        amount=Decimal("200.00"),
        status=AffiliateQuestionStatus.PENDING,
        answered_at=None,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


@pytest.fixture
def world(monkeypatch) -> World:
    w = World()
    affiliate = SimpleNamespace(
        id=uuid.uuid4(), name="Prof. Ana", points_rate=Decimal("50")
    )

    @asynccontextmanager
    async def _session_cm():
        yield w.session

    async def fake_find_customer(session, phone_n):
        return SimpleNamespace(id=CUSTOMER_ID, name="Andre")

    async def fake_get_pending(session, customer_id, window_days):
        # Imita o filtro do banco: só PENDING (a janela é do SQL real).
        w.window_days_seen.append(window_days)
        q = w.question
        if q is not None and q.status is AffiliateQuestionStatus.PENDING:
            return q
        return None

    async def fake_get_affiliate(session, code):
        return affiliate if code.strip().upper() == AFF_CODE else None

    async def fake_record_attribution(session, **kwargs):
        w.attributions.append(kwargs)
        return SimpleNamespace(**kwargs)

    async def fake_is_first_purchase(session, customer_id, source_reference):
        return w.first_purchase

    async def fake_get_balance(session, customer_id):
        return BALANCE

    monkeypatch.setattr(conversation, "find_customer_by_phone", fake_find_customer)
    monkeypatch.setattr(
        conversation, "get_pending_affiliate_question", fake_get_pending
    )
    monkeypatch.setattr(
        conversation, "get_active_affiliate_by_code", fake_get_affiliate
    )
    monkeypatch.setattr(conversation, "record_attribution", fake_record_attribution)
    monkeypatch.setattr(conversation, "is_first_purchase", fake_is_first_purchase)
    monkeypatch.setattr(conversation, "get_balance", fake_get_balance)
    w.session_factory = _session_cm  # type: ignore[attr-defined]
    return w


@pytest.fixture
def store() -> InMemorySessionStore:
    return InMemorySessionStore()


async def _send(world: World, store, text: str) -> list[str]:
    return await handle_message(PHONE, text, store, world.session_factory)


async def _code_requested(store) -> None:
    await store.set(
        PHONE, ConversationState(step=ConversationStep.AWAITING_AFFILIATE_CODE)
    )


async def test_late_yes_without_session_asks_for_code(world, store):
    """O "Sim" horas depois (sessão já expirada) ainda pede o código."""
    world.question = _question()

    out = await _send(world, store, "Sim")

    assert out == [messages.ask_affiliate_code_after_yes()]
    state = await store.get(PHONE)
    assert state is not None
    assert state.step is ConversationStep.AWAITING_AFFILIATE_CODE
    assert world.question.status is AffiliateQuestionStatus.PENDING
    assert world.attributions == []


async def test_code_after_yes_attributes_with_question_data(world, store):
    world.question = _question()
    await _code_requested(store)

    out = await _send(world, store, "prof-ana")

    assert out == [messages.affiliate_attributed("Prof. Ana")]
    # Compra, pontos e valor vêm da PERGUNTA no banco, não da sessão.
    assert len(world.attributions) == 1
    attr = world.attributions[0]
    assert attr["customer_id"] == CUSTOMER_ID
    assert attr["source_reference"] == "TX-42"
    assert attr["points"] == 120
    assert attr["amount"] == Decimal("200.00")
    assert attr["affiliate_points"] == 100  # floor(200 × 50%)
    assert world.question.status is AffiliateQuestionStatus.ATTRIBUTED
    assert world.question.answered_at is not None
    assert world.session.commits == 1
    assert await store.get(PHONE) is None


async def test_code_on_repeat_purchase_has_zero_commission(world, store):
    """Trava de comissão: fora da primeira compra, atribui com zero pontos."""
    world.question = _question()
    world.first_purchase = False
    await _code_requested(store)

    await _send(world, store, AFF_CODE)

    assert world.attributions[0]["affiliate_points"] == 0
    assert world.question.status is AffiliateQuestionStatus.ATTRIBUTED


async def test_code_sent_directly_attributes(world, store):
    """Quem manda o código sem clicar "Sim" antes também é atendido."""
    world.question = _question()

    out = await _send(world, store, AFF_CODE)

    assert out == [messages.affiliate_attributed("Prof. Ana")]
    assert world.question.status is AffiliateQuestionStatus.ATTRIBUTED


@pytest.mark.parametrize("text", ["Não", "nao", "pular"])
async def test_no_declines_question(world, store, text):
    world.question = _question()

    out = await _send(world, store, text)

    assert out == [messages.affiliate_skipped()]
    assert world.question.status is AffiliateQuestionStatus.DECLINED
    assert world.question.answered_at is not None
    assert world.session.commits == 1
    assert world.attributions == []


async def test_balance_is_not_hijacked_by_pending_question(world, store):
    world.question = _question()

    for text in ("saldo", "1"):
        out = await _send(world, store, text)
        assert out == [messages.balance(BALANCE)]

    assert world.question.status is AffiliateQuestionStatus.PENDING
    assert await store.get(PHONE) is None


async def test_yes_without_pending_question_goes_to_menu(world, store):
    out = await _send(world, store, "sim")

    assert out == [messages.menu()]
    assert await store.get(PHONE) is None


async def test_unknown_code_after_request_keeps_step(world, store):
    world.question = _question()
    await _code_requested(store)

    out = await _send(world, store, "CODIGO-INEXISTENTE")

    assert out == [messages.affiliate_code_not_found()]
    state = await store.get(PHONE)
    assert state is not None
    assert state.step is ConversationStep.AWAITING_AFFILIATE_CODE
    assert world.question.status is AffiliateQuestionStatus.PENDING


async def test_yes_after_code_requested_is_treated_as_code(world, store):
    """Depois de pedido o código, "sim" é código (protege afiliado "SIM")."""
    world.question = _question()
    await _code_requested(store)

    out = await _send(world, store, "sim")

    assert out == [messages.affiliate_code_not_found()]
    state = await store.get(PHONE)
    assert state is not None
    assert state.step is ConversationStep.AWAITING_AFFILIATE_CODE


async def test_unknown_text_without_request_is_not_an_answer(world, store):
    """Sem código pedido, texto que não é código cai no menu, sem "não achei"."""
    world.question = _question()

    out = await _send(world, store, "oi")

    assert out == [messages.menu()]
    assert world.question.status is AffiliateQuestionStatus.PENDING


async def test_step_without_pending_question_falls_back_to_normal_flow(
    world, store
):
    """Passo órfão (pergunta respondida em outro lugar ou fora da janela)."""
    world.question = _question(status=AffiliateQuestionStatus.ATTRIBUTED)
    await _code_requested(store)

    out = await _send(world, store, "saldo")

    assert out == [messages.balance(BALANCE)]
    assert await store.get(PHONE) is None
    assert world.attributions == []


async def test_menu_while_code_requested_keeps_question_pending(world, store):
    """"menu" mostra o menu e NÃO recusa a indicação para sempre."""
    world.question = _question()
    await _code_requested(store)

    out = await _send(world, store, "menu")

    assert out == [messages.menu()]
    assert world.question.status is AffiliateQuestionStatus.PENDING
    assert world.question.answered_at is None
    assert await store.get(PHONE) is None
    assert world.session.commits == 0

    # E a pergunta continua respondível depois.
    out = await _send(world, store, "Sim")
    assert out == [messages.ask_affiliate_code_after_yes()]


async def test_menu_without_request_does_not_decline(world, store):
    world.question = _question()

    out = await _send(world, store, "menu")

    assert out == [messages.menu()]
    assert world.question.status is AffiliateQuestionStatus.PENDING


async def test_uses_configured_answer_window(world, store):
    from app.config import get_settings

    world.question = _question()
    await _send(world, store, "Sim")

    assert world.window_days_seen
    assert set(world.window_days_seen) == {
        get_settings().affiliate_answer_window_days
    }
