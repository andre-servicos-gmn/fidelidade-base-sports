"""Testes do reconhecimento de intenção por palavra-chave.

Problema real observado em campo: o cliente escreveu "quero resgatar um cupom"
e o bot abriu o MENU, porque só casava comando exato (`cmd in _REDEEM_CMDS`).
O cliente pede uma coisa e recebe outra, sem entender por quê.

Agora `_detect_intent` tenta o comando exato e, se não casar, procura
PALAVRA-CHAVE na frase — com duas salvaguardas testadas aqui:
- casa por palavra inteira (não substring), evitando falso positivo;
- frase negativa ("não quero resgatar") NÃO dispara a ação.
"""

from __future__ import annotations

import pytest

from app.whatsapp.conversation import _detect_intent


def _intent(text: str) -> str | None:
    return _detect_intent(text, text.lower())


# --------------------------------------------------------------------------- #
# Comandos exatos (comportamento antigo, deve continuar valendo)               #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text,expected",
    [
        ("1", "balance"),
        ("saldo", "balance"),
        ("2", "redeem"),
        ("resgatar", "redeem"),
        ("3", "coupons"),
        ("cupons", "coupons"),
        ("menu", "menu"),
        ("ajuda", "menu"),
    ],
)
def test_exact_commands_still_work(text: str, expected: str):
    assert _intent(text) == expected


# --------------------------------------------------------------------------- #
# Linguagem natural (o bug relatado)                                           #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text,expected",
    [
        # O caso exato que falhou em campo.
        ("quero resgatar um cupom", "redeem"),
        ("gostaria de resgatar", "redeem"),
        ("quero trocar meus pontos por um premio", "redeem"),
        ("quais as recompensas?", "redeem"),
        ("ver meu saldo", "balance"),
        ("quantos pontos eu tenho", "balance"),
        ("qual meu extrato", "balance"),
        ("meus cupons", "coupons"),
        ("quero ver meus descontos", "coupons"),
        ("me ve o menu", "menu"),
        ("preciso de ajuda", "menu"),
    ],
)
def test_natural_language_is_understood(text: str, expected: str):
    assert _intent(text) == expected


# --------------------------------------------------------------------------- #
# Salvaguardas: o que NÃO pode disparar ação                                   #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text",
    [
        "nao quero resgatar nada",
        "não quero resgatar",
        "sem cupons por enquanto",
        "oi",
        "bom dia",
        "kkk",
        "obrigado",
    ],
)
def test_no_false_positive(text: str):
    """Negação e saudações não podem disparar uma ação (cai no menu)."""
    assert _intent(text) is None


def test_redeem_wins_over_coupons_when_both_present():
    """'resgatar um cupom' tem as duas palavras — a intenção é RESGATAR."""
    assert _intent("quero resgatar um cupom") == "redeem"


# --------------------------------------------------------------------------- #
# Saída do passo de escolha de recompensa                                      #
# --------------------------------------------------------------------------- #
async def test_other_intent_escapes_reward_choice_step(monkeypatch):
    """Dentro da escolha de recompensa, pedir OUTRA coisa deve ser atendido.

    Antes, "meus cupons" no meio da escolha respondia "Não entendi a opção" e
    o cliente ficava preso sem saber que precisava mandar "menu".
    """
    from contextlib import asynccontextmanager

    from app.whatsapp import conversation, messages
    from app.whatsapp.conversation import handle_message
    from app.whatsapp.session_store import (
        ConversationState,
        ConversationStep,
        InMemorySessionStore,
    )

    phone = "11955554444"

    class _Customer:
        id = "cust-1"
        name = "Teste"

    class _Result:
        def scalar_one_or_none(self):
            return _Customer()

        def scalars(self):
            return self

        def all(self):
            return []  # cliente sem cupons

    class _Session:
        async def execute(self, *_a, **_k):
            return _Result()

        async def commit(self):
            pass

        async def rollback(self):
            pass

    @asynccontextmanager
    async def _factory():
        yield _Session()

    # A sessão fake devolve o cliente para QUALQUER consulta; sem isto ele
    # viraria também uma "pergunta de indicação pendente".
    async def _no_pending_question(*_a, **_k):
        return None

    monkeypatch.setattr(
        conversation, "get_pending_affiliate_question", _no_pending_question
    )

    store = InMemorySessionStore()
    # Cliente está escolhendo a recompensa.
    await store.set(
        phone,
        ConversationState(
            step=ConversationStep.AWAITING_REWARD_CHOICE,
            data={"rewards": [{"reward_id": "r1"}]},
        ),
    )

    # Pede outra coisa em linguagem natural.
    replies = await handle_message(phone, "meus cupons", store, _factory)

    # NÃO respondeu "não entendi": saiu do passo e atendeu o pedido.
    assert replies != [messages.invalid_choice()]
    assert await store.get(phone) is None  # saiu do passo de escolha
