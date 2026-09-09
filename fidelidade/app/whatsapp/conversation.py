"""Máquina de conversa do WhatsApp: (telefone, texto) -> respostas.

100% testável sem WhatsApp real. A próxima fase (Evolution API) será só um
adapter fino que chama `handle_message` e envia de volta a lista de mensagens.

Cada chamada usa a SUA própria sessão de banco (via `session_factory`), nunca
reaproveitando sessão entre mensagens — isso evita o problema de objeto ORM
expirado visto na Fase 4 (quando um rollback interno expira os objetos).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy.ext.asyncio import AsyncSession

from decimal import Decimal

from app.domain.redemption import InsufficientPointsError, NoCouponAvailableError
from app.services.affiliate_service import (
    compute_affiliate_points,
    get_active_affiliate_by_code,
    record_attribution,
)
from app.domain.security import is_valid_cpf
from app.services.identity_service import (
    PhoneAlreadyLinkedError,
    find_customer_by_phone,
    normalize_phone,
    register_customer_by_cpf,
)
from app.services.ledger_service import get_balance, is_first_purchase
from app.services.redemption_service import (
    get_customer_coupons,
    list_available_rewards,
    redeem_coupon,
)
from app.whatsapp import messages
from app.whatsapp.session_store import (
    ConversationState,
    ConversationStep,
    SessionStore,
)

# Uma fábrica de sessão: chamá-la abre uma nova AsyncSession (context manager).
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

_BALANCE_CMDS = {"saldo", "1"}
_REDEEM_CMDS = {"resgatar", "2"}
_COUPONS_CMDS = {"cupons", "3"}
_MENU_CMDS = {"menu", "ajuda"}

# Encerramento GLOBAL: vale em qualquer passo, para cliente cadastrado ou não.
# "sair" saiu de `_MENU_CMDS` — antes ele mostrava o menu, o oposto do que o
# cliente pediu. "não" NÃO entra aqui: nos passos de escolha ele significa a
# opção 2 (não participar / não concordar), não "encerrar".
_EXIT_CMDS = {"sair", "cancelar", "parar", "para", "stop", "desistir", "encerrar"}

# Respostas aceitas nos passos de escolha do onboarding.
_YES_CMDS = {"1", "sim", "s", "quero", "aceito"}
_NO_CMDS = {"2", "nao", "não", "n", "nao quero", "não quero"}
_READ_FULL_CMDS = {"3", "ler", "regulamento", "completo", "ler completo"}

# --------------------------------------------------------------------------- #
# Reconhecimento de intenção por PALAVRA-CHAVE                                 #
# --------------------------------------------------------------------------- #
# O cliente escreve em linguagem natural ("quero resgatar um cupom"), não o
# comando exato. Casar só por igualdade fazia tudo cair no menu genérico — o
# cliente pedia uma coisa e recebia outra, sem entender por quê.
#
# Casamos por PALAVRA INTEIRA (não substring) para evitar falso positivo, e
# checamos negação antes, para "não quero resgatar" não disparar o resgate.
_BALANCE_WORDS = {"saldo", "pontos", "pontuacao", "pontuação", "extrato"}
_REDEEM_WORDS = {"resgatar", "resgate", "trocar", "troca", "recompensa",
                 "recompensas", "premio", "prêmio", "premios", "prêmios"}
_COUPONS_WORDS = {"cupom", "cupons", "desconto", "descontos", "voucher"}
_MENU_WORDS = {"menu", "ajuda", "opcoes", "opções", "inicio", "início"}
# Se a frase nega, não inferimos intenção (deixa cair no menu/fallback).
_NEGATION_WORDS = {"nao", "não", "nunca", "sem"}

_WORD_SPLIT = re.compile(r"[^0-9a-záàâãéêíóôõúüç]+", re.IGNORECASE)


def _words(text: str) -> set[str]:
    """Quebra a frase em palavras minúsculas (para casar por palavra inteira)."""
    return {w for w in _WORD_SPLIT.split(text.lower()) if w}


def _has_negation(words: set[str]) -> bool:
    return bool(words & _NEGATION_WORDS)


def _detect_intent(text_clean: str, cmd: str) -> str | None:
    """Detecta a intenção do cliente: 'balance' | 'redeem' | 'coupons' | 'menu'.

    Primeiro tenta o comando EXATO (atalhos "1"/"2"/"3" e a palavra seca);
    depois tenta por palavra-chave dentro da frase. Retorna None quando não dá
    para inferir com segurança — aí o chamador cai no menu.
    """
    # 1) Comando exato (inclui os atalhos numéricos).
    if cmd in _BALANCE_CMDS:
        return "balance"
    if cmd in _REDEEM_CMDS:
        return "redeem"
    if cmd in _COUPONS_CMDS:
        return "coupons"
    if cmd in _MENU_CMDS:
        return "menu"

    # 2) Palavra-chave na frase. Negação desliga a inferência.
    words = _words(text_clean)
    if _has_negation(words):
        return None
    # Ordem importa: "cupom" é mais específico que "resgatar"; mas quem diz
    # "quero resgatar um cupom" quer RESGATAR, então resgate vem antes.
    if words & _REDEEM_WORDS:
        return "redeem"
    if words & _COUPONS_WORDS:
        return "coupons"
    if words & _BALANCE_WORDS:
        return "balance"
    if words & _MENU_WORDS:
        return "menu"
    return None


# Após esta quantidade de respostas inválidas no passo do CPF, o bot para de
# insistir e encerra educadamente (em vez de repetir a mesma mensagem sem fim).
_MAX_CPF_ATTEMPTS = 3
# Respostas que pulam a pergunta do código de afiliado (sem indicação).
# "sair"/"cancelar" saíram: agora encerram a conversa (`_EXIT_CMDS`), tratados
# antes de chegar aqui.
_AFFILIATE_SKIP_CMDS = {
    "não",
    "nao",
    "não tenho",
    "nao tenho",
    "menu",
    "pular",
}

# Respostas afirmativas à pergunta de afiliado. O clique num botão do template
# chega como o RÓTULO do botão, então estes valores precisam bater com o texto
# aprovado na Meta — mudou o rótulo lá, mude aqui. As formas secas ("sim"/"s")
# cobrem quem responde digitando, na Evolution ou já dentro da janela de 24h.
_AFFILIATE_YES_CMDS = {
    "sim",
    "s",
    "sim, tenho o código",
    "sim, tenho o codigo",
    "tenho o código",
    "tenho o codigo",
}


async def handle_message(
    phone: str,
    text: str,
    store: SessionStore,
    session_factory: SessionFactory,
) -> list[str]:
    """Processa uma mensagem recebida e devolve as respostas (1+ balões)."""
    phone_n = normalize_phone(phone)
    text_clean = text.strip()
    cmd = text_clean.lower()

    # Encerramento GLOBAL, antes de qualquer passo: "sair" tem que funcionar em
    # qualquer ponto da conversa, cadastrado ou não. Não abre sessão de banco
    # para isso — é só limpar o estado e responder.
    if cmd in _EXIT_CMDS:
        await store.delete(phone_n)
        return [messages.goodbye()]

    async with session_factory() as session:
        customer = await find_customer_by_phone(session, phone_n)
        if customer is None:
            return await _handle_unregistered(
                session, store, phone_n, text_clean
            )
        # Captura o id ANTES de qualquer operação que possa dar rollback
        # interno (resgate), evitando acessar atributo de ORM expirado.
        customer_id = customer.id
        customer_name = customer.name
        return await _handle_registered(
            session, store, phone_n, customer_id, customer_name, text_clean, cmd
        )


async def _handle_unregistered(
    session: AsyncSession,
    store: SessionStore,
    phone_n: str,
    text_clean: str,
) -> list[str]:
    """Onboarding do Base Club, em três passos.

        primeiro contato -> quer participar? -> aceita o regulamento? -> CPF

    A ordem não é estética: o aceite é pedido ANTES do CPF porque não se coleta
    dado pessoal sem consentimento registrado. O aceite é gravado em
    `terms_acceptances` na mesma transação do cadastro.
    """
    state = await store.get(phone_n)
    step = state.step if state is not None else None
    cmd = text_clean.lower()

    # --- Passo 1: quer participar? ---------------------------------------- #
    if step is ConversationStep.AWAITING_JOIN_CHOICE:
        if cmd in _NO_CMDS:
            await store.delete(phone_n)
            return [messages.goodbye()]
        if cmd in _YES_CMDS:
            await store.set(
                phone_n,
                ConversationState(step=ConversationStep.AWAITING_TERMS_CONSENT),
            )
            return [messages.terms_summary()]
        return [messages.invalid_option()]

    # --- Passo 2: aceita o regulamento? ----------------------------------- #
    if step is ConversationStep.AWAITING_TERMS_CONSENT:
        if cmd in _NO_CMDS:
            await store.delete(phone_n)
            return [messages.terms_declined()]
        if cmd in _READ_FULL_CMDS:
            # Mantém o passo: depois de ler, ele ainda precisa aceitar.
            return [messages.terms_full()]
        if cmd in _YES_CMDS:
            await store.set(
                phone_n,
                ConversationState(step=ConversationStep.AWAITING_CPF),
            )
            return [messages.ask_cpf()]
        return [messages.invalid_option()]

    # --- Passo 3: o CPF ---------------------------------------------------- #
    if step is ConversationStep.AWAITING_CPF:
        # Valida os DÍGITOS VERIFICADORES, não só o tamanho: como o cadastro
        # agora é criado aqui, um dígito errado geraria um cliente fantasma que
        # nunca casaria com compra nenhuma.
        if not is_valid_cpf(text_clean):
            # Escalona em vez de repetir a MESMA frase indefinidamente.
            attempts = int(state.data.get("cpf_attempts", 0)) + 1
            if attempts >= _MAX_CPF_ATTEMPTS:
                await store.delete(phone_n)
                return [messages.cpf_give_up()]
            await store.set(
                phone_n,
                ConversationState(
                    step=ConversationStep.AWAITING_CPF,
                    data={**state.data, "cpf_attempts": attempts},
                ),
            )
            if attempts == 1:
                return [messages.cpf_invalid()]
            return [messages.cpf_invalid_again()]

        try:
            customer, created = await register_customer_by_cpf(
                session, phone_n, text_clean, messages.TERMS_VERSION
            )
        except PhoneAlreadyLinkedError:
            await store.delete(phone_n)
            return [messages.phone_already_linked()]

        # CPF novo entra com saldo zero; CPF que já comprava traz o saldo dele.
        # `expire_on_commit=False` na factory, então ler os atributos após o
        # commit não dispara consulta preguiçosa.
        saldo = 0 if created else await get_balance(session, customer.id)

        await store.delete(phone_n)
        return [
            messages.welcome_to_program(saldo, customer.name),
            messages.menu(),
        ]

    # --- Primeiro contato -------------------------------------------------- #
    await store.set(
        phone_n, ConversationState(step=ConversationStep.AWAITING_JOIN_CHOICE)
    )
    return [messages.greeting()]


async def _handle_registered(
    session: AsyncSession,
    store: SessionStore,
    phone_n: str,
    customer_id,
    customer_name: str | None,
    text_clean: str,
    cmd: str,
) -> list[str]:
    state = await store.get(phone_n)

    # Após uma compra, perguntamos o código de afiliado: a resposta é o código
    # (ou "não"), não um atalho de menu. Tem precedência sobre os comandos.
    if state is not None and state.step is ConversationStep.AWAITING_AFFILIATE_CODE:
        return await _handle_affiliate_code(
            session, store, phone_n, customer_id, state, text_clean, cmd
        )

    # No passo de escolha de recompensa, o número é a escolha (não atalho).
    if state is not None and state.step is ConversationStep.AWAITING_REWARD_CHOICE:
        if cmd in _MENU_CMDS:
            await store.delete(phone_n)
            return [messages.menu()]
        # Se NÃO é um número e o cliente claramente pede OUTRA coisa ("meus
        # cupons", "meu saldo"), atendemos em vez de responder "não entendi" —
        # senão ele fica preso no passo sem saber que precisa mandar "menu".
        if not text_clean.strip().isdigit():
            other = _detect_intent(text_clean, cmd)
            if other in ("balance", "coupons"):
                await store.delete(phone_n)
                return await _handle_registered(
                    session, store, phone_n, customer_id, customer_name,
                    text_clean, cmd,
                )
        return await _handle_reward_choice(
            session, store, phone_n, customer_id, state, text_clean
        )

    # Entende o comando exato E a frase natural ("quero resgatar um cupom").
    intent = _detect_intent(text_clean, cmd)

    if intent == "balance":
        points = await get_balance(session, customer_id)
        return [messages.balance(points)]

    if intent == "redeem":
        rewards = await list_available_rewards(session)
        if not rewards:
            return [messages.no_rewards()]
        await store.set(
            phone_n,
            ConversationState(
                step=ConversationStep.AWAITING_REWARD_CHOICE,
                data={"rewards": rewards},
            ),
        )
        return [messages.rewards_list(rewards)]

    if intent == "coupons":
        coupons = await get_customer_coupons(session, customer_id)
        if not coupons:
            return [messages.no_coupons()]
        return [messages.coupons_list(coupons)]

    # "menu"/"ajuda"/"oi"/qualquer coisa não reconhecida -> menu.
    return [messages.menu()]


async def _handle_affiliate_code(
    session: AsyncSession,
    store: SessionStore,
    phone_n: str,
    customer_id,
    state: ConversationState,
    text_clean: str,
    cmd: str,
) -> list[str]:
    """Captura o código de afiliado e atribui a compra pendente do `state`."""
    # "não"/"menu"/"pular" -> segue sem indicação.
    if cmd in _AFFILIATE_SKIP_CMDS:
        await store.delete(phone_n)
        return [messages.affiliate_skipped()]

    # Botão "Sim": o clique NÃO é um código — é o cliente dizendo que TEM um.
    # Sem este ramo, "Sim" seguiria para `get_active_affiliate_by_code`, não
    # acharia nada e o cliente levaria "código não encontrado" por ter clicado
    # no botão certo. Pergunta o código e MANTÉM o passo.
    #
    # `code_requested` limita a interpretação ao primeiro turno: depois de
    # pedirmos o código, um "sim" seguinte volta a ser tratado como código —
    # protege o caso (improvável, mas possível) de um afiliado cujo código
    # seja literalmente "sim".
    if not state.data.get("code_requested") and cmd in _AFFILIATE_YES_CMDS:
        await store.set(
            phone_n,
            ConversationState(
                step=ConversationStep.AWAITING_AFFILIATE_CODE,
                data={**state.data, "code_requested": True},
            ),
        )
        return [messages.ask_affiliate_code_after_yes()]

    source_reference = state.data.get("source_reference")
    points = int(state.data.get("points", 0))
    amount = Decimal(str(state.data.get("amount", "0")))
    # Sem a compra de referência não há o que atribuir (estado inconsistente).
    if not source_reference:
        await store.delete(phone_n)
        return [messages.affiliate_skipped()]

    affiliate = await get_active_affiliate_by_code(session, text_clean)
    if affiliate is None:
        # Código desconhecido: mantém o passo para nova tentativa ou "não".
        return [messages.affiliate_code_not_found()]

    # Captura nome/id e calcula os pontos do afiliado ANTES do commit (evita
    # acessar atributos de ORM expirado depois).
    affiliate_name = affiliate.name

    # TRAVA DE COMISSÃO: o afiliado ganha pontos só na PRIMEIRA compra daquele
    # CPF. Compras seguintes ainda são atribuídas (a informação de quem indicou
    # tem valor), mas com comissão zero.
    #
    # A checagem é refeita aqui, contra o ledger, mesmo a ingestão já só
    # perguntando na primeira compra: aquilo é UX, isto é a garantia. Sem esta
    # linha, um estado antigo na sessão ou uma mensagem fora de hora poderia
    # gerar comissão indevida — e comissão indevida é dinheiro.
    if await is_first_purchase(session, customer_id, source_reference):
        affiliate_points = compute_affiliate_points(amount, affiliate.points_rate)
    else:
        affiliate_points = 0
    await record_attribution(
        session,
        affiliate_id=affiliate.id,
        customer_id=customer_id,
        source_reference=source_reference,
        points=points,
        amount=amount,
        affiliate_points=affiliate_points,
    )
    await session.commit()

    await store.delete(phone_n)
    return [messages.affiliate_attributed(affiliate_name)]


async def _handle_reward_choice(
    session: AsyncSession,
    store: SessionStore,
    phone_n: str,
    customer_id,
    state: ConversationState,
    text_clean: str,
) -> list[str]:
    rewards = state.data.get("rewards", [])
    choice = text_clean.strip()

    if not choice.isdigit():
        return [messages.invalid_choice()]
    index = int(choice) - 1
    if index < 0 or index >= len(rewards):
        return [messages.invalid_choice()]

    reward = rewards[index]
    try:
        result = await redeem_coupon(session, customer_id, reward["reward_id"])
    except InsufficientPointsError as exc:
        await store.delete(phone_n)
        return [messages.insufficient_points(exc.balance, exc.cost)]
    except NoCouponAvailableError:
        await store.delete(phone_n)
        return [messages.reward_unavailable()]

    await store.delete(phone_n)
    return [messages.redemption_success(result)]
