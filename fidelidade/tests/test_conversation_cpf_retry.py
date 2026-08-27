"""Testes do onboarding do Base Club (fluxo de adesão + aceite + CPF).

O fluxo tem três passos, nesta ordem:

    primeiro contato -> "quer participar?" -> "aceita o regulamento?" -> CPF

O aceite vem ANTES do CPF de propósito: não se coleta dado pessoal sem
consentimento registrado.

Também cobre o comportamento no passo do CPF, que veio de um problema real de
campo: o bot repetia a MESMA frase ("Esse CPF não parece válido") para qualquer
texto que não fosse um CPF, sem oferecer saída. Tratamento testado aqui:

- `sair` encerra em QUALQUER passo (encerramento global);
- a mensagem ESCALONA em vez de repetir (1ª -> orienta, 2ª -> exemplo + saída);
- após `_MAX_CPF_ATTEMPTS` o bot encerra educadamente;
- CPF com 11 dígitos mas dígito verificador errado é recusado (antes passava, e
  agora passaria a CRIAR um cadastro fantasma).

Puros: o cliente não existe, então nenhum caminho aqui chega a gravar no banco —
usamos um session_factory fake. O cadastro em si é coberto por teste de
integração em `test_conversation.py`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.whatsapp import messages
from app.whatsapp.conversation import _MAX_CPF_ATTEMPTS, handle_message
from app.whatsapp.session_store import (
    ConversationStep,
    InMemorySessionStore,
)

PHONE = "11999998888"
# CPF com dígitos verificadores VÁLIDOS (não existe na base dos testes).
VALID_CPF = "42317184824"
# 11 dígitos, mas dígito verificador errado.
BAD_CHECK_DIGIT_CPF = "42317184825"


class _FakeResult:
    def scalar_one_or_none(self):
        return None  # nenhum cliente com esse telefone/CPF

    def scalars(self):
        return self

    def first(self):
        return None


class _FakeSession:
    """Sessão que responde 'não encontrei' a qualquer consulta."""

    async def execute(self, *_args, **_kwargs):
        return _FakeResult()

    async def commit(self):
        pass

    async def rollback(self):
        pass


@asynccontextmanager
async def _fake_session_cm():
    yield _FakeSession()


def _fake_factory():
    return _fake_session_cm()


async def _send(store, text: str) -> list[str]:
    return await handle_message(PHONE, text, store, _fake_factory)


async def _walk_to_terms(store) -> list[str]:
    """Primeiro contato + aceita participar -> fica no passo do regulamento."""
    await _send(store, "oi")
    return await _send(store, "1")


async def _walk_to_cpf(store) -> list[str]:
    """Vai até o passo do CPF, aceitando participar e o regulamento."""
    await _walk_to_terms(store)
    return await _send(store, "1")


# --------------------------------------------------------------------------- #
# Passo 1: quer participar?                                                    #
# --------------------------------------------------------------------------- #
async def test_first_contact_asks_to_join():
    store = InMemorySessionStore()
    replies = await _send(store, "oi")

    assert replies == [messages.greeting()]
    state = await store.get(PHONE)
    assert state is not None
    assert state.step is ConversationStep.AWAITING_JOIN_CHOICE


@pytest.mark.parametrize("no_word", ["2", "não", "nao", "n"])
async def test_declining_to_join_ends_conversation(no_word: str):
    store = InMemorySessionStore()
    await _send(store, "oi")

    replies = await _send(store, no_word)

    assert replies == [messages.goodbye()]
    assert await store.get(PHONE) is None


@pytest.mark.parametrize("yes_word", ["1", "sim", "s"])
async def test_accepting_join_shows_terms_summary(yes_word: str):
    store = InMemorySessionStore()
    await _send(store, "oi")

    replies = await _send(store, yes_word)

    assert replies == [messages.terms_summary()]
    state = await store.get(PHONE)
    assert state is not None
    assert state.step is ConversationStep.AWAITING_TERMS_CONSENT


async def test_unrecognized_answer_does_not_advance_the_step():
    """Resposta fora das opções não pode empurrar o cliente para o passo seguinte."""
    store = InMemorySessionStore()
    await _send(store, "oi")

    replies = await _send(store, "talvez depois")

    assert replies == [messages.invalid_option()]
    state = await store.get(PHONE)
    assert state is not None
    assert state.step is ConversationStep.AWAITING_JOIN_CHOICE


# --------------------------------------------------------------------------- #
# Passo 2: aceite do regulamento                                               #
# --------------------------------------------------------------------------- #
async def test_can_read_full_terms_and_still_has_to_accept():
    store = InMemorySessionStore()
    await _walk_to_terms(store)

    replies = await _send(store, "3")

    assert replies == [messages.terms_full()]
    # Ler não é aceitar: continua no mesmo passo.
    state = await store.get(PHONE)
    assert state is not None
    assert state.step is ConversationStep.AWAITING_TERMS_CONSENT


async def test_declining_terms_ends_without_asking_cpf():
    store = InMemorySessionStore()
    await _walk_to_terms(store)

    replies = await _send(store, "2")

    assert replies == [messages.terms_declined()]
    assert await store.get(PHONE) is None
    # Nada de CPF foi pedido: sem aceite, não se coleta dado pessoal.
    assert "CPF" not in replies[0]


async def test_accepting_terms_asks_for_cpf():
    store = InMemorySessionStore()
    await _walk_to_terms(store)

    replies = await _send(store, "1")

    assert replies == [messages.ask_cpf()]
    assert "CPF" in replies[0]
    state = await store.get(PHONE)
    assert state is not None and state.step is ConversationStep.AWAITING_CPF


# --------------------------------------------------------------------------- #
# Encerramento global                                                          #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("quit_word", ["sair", "cancelar", "parar", "STOP"])
async def test_exit_command_works_from_the_join_step(quit_word: str):
    store = InMemorySessionStore()
    await _send(store, "oi")

    replies = await _send(store, quit_word)

    assert replies == [messages.goodbye()]
    assert await store.get(PHONE) is None


async def test_exit_works_from_every_onboarding_step():
    """'sair' precisa funcionar em qualquer ponto, não só no passo do CPF."""
    for walk in (_walk_to_terms, _walk_to_cpf):
        store = InMemorySessionStore()
        await walk(store)

        replies = await _send(store, "sair")

        assert replies == [messages.goodbye()]
        assert await store.get(PHONE) is None


async def test_exit_without_any_previous_message():
    """'sair' de quem nunca falou com o bot também encerra, sem erro."""
    store = InMemorySessionStore()

    replies = await _send(store, "sair")

    assert replies == [messages.goodbye()]


# --------------------------------------------------------------------------- #
# Passo 3: o CPF                                                               #
# --------------------------------------------------------------------------- #
async def test_invalid_messages_escalate_instead_of_repeating():
    """A 2ª mensagem inválida NÃO pode ser igual à 1ª (o bug observado)."""
    store = InMemorySessionStore()
    await _walk_to_cpf(store)

    first = await _send(store, "Beleza")
    second = await _send(store, "kkk")

    assert first == [messages.cpf_invalid()]
    assert second == [messages.cpf_invalid_again()]
    assert first != second  # o ponto do teste: não repete a mesma frase


async def test_gives_up_after_max_attempts():
    """Após N tentativas inválidas, encerra em vez de insistir para sempre."""
    store = InMemorySessionStore()
    await _walk_to_cpf(store)

    replies = []
    for i in range(_MAX_CPF_ATTEMPTS):
        replies = await _send(store, f"texto invalido {i}")

    assert replies == [messages.cpf_give_up()]
    assert await store.get(PHONE) is None


async def test_cpf_with_wrong_check_digit_is_rejected():
    """11 dígitos não basta: dígito verificador errado é erro de digitação.

    Sem isso, o autocadastro criaria um cliente que nunca casaria com nenhuma
    compra do totem — um fantasma no banco.
    """
    store = InMemorySessionStore()
    await _walk_to_cpf(store)

    replies = await _send(store, BAD_CHECK_DIGIT_CPF)

    assert replies == [messages.cpf_invalid()]
    state = await store.get(PHONE)
    assert state is not None and state.step is ConversationStep.AWAITING_CPF


async def test_attempt_counter_survives_between_invalid_tries():
    """O contador acumula: 1ª orienta, 2ª dá exemplo, 3ª encerra."""
    store = InMemorySessionStore()
    await _walk_to_cpf(store)

    assert await _send(store, "aaa") == [messages.cpf_invalid()]
    assert await _send(store, "bbb") == [messages.cpf_invalid_again()]
    assert await _send(store, "ccc") == [messages.cpf_give_up()]
