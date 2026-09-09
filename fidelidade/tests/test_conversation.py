"""Testes da máquina de conversa do WhatsApp.

- Pura (normalização, session store, formatação): sem banco.
- Fluxos completos (cadastro, saldo, resgate): @pytest.mark.integration.
"""

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db.models import (
    Affiliate,
    AffiliateAttribution,
    AffiliateType,
    CouponDiscountType,
    CouponPool,
    CouponStatus,
    Customer,
    LedgerEntry,
    LedgerEntryType,
    TermsAcceptance,
)
from app.domain.security import hash_cpf
from app.services.customer_service import get_or_create_customer
from app.services.identity_service import (
    find_customer_by_phone,
    link_phone_to_cpf,
    normalize_phone,
)
from app.services.ledger_service import add_entry, get_balance
from app.services.redemption_service import make_reward_id
from app.whatsapp import messages
from app.whatsapp.conversation import handle_message
from app.whatsapp.session_store import (
    ConversationState,
    ConversationStep,
    InMemorySessionStore,
)

# --------------------------------------------------------------------------- #
# Testes PUROS (sem banco)                                                     #
# --------------------------------------------------------------------------- #
def test_normalize_phone():
    assert normalize_phone("+55 (11) 99000-0001") == "11990000001"
    assert normalize_phone("5511990000001") == "11990000001"
    assert normalize_phone("11990000001") == "11990000001"
    assert normalize_phone("") == ""


async def test_session_store_ttl_expires():
    now = {"t": 1000.0}
    store = InMemorySessionStore(ttl_seconds=60, clock=lambda: now["t"])

    await store.set("p1", ConversationState(step=ConversationStep.AWAITING_CPF))
    state = await store.get("p1")
    assert state is not None and state.step is ConversationStep.AWAITING_CPF

    now["t"] += 59  # ainda dentro do TTL
    assert await store.get("p1") is not None

    now["t"] += 2  # passou do TTL (61s desde o set)
    assert await store.get("p1") is None


async def test_session_store_delete():
    store = InMemorySessionStore()
    await store.set("p1", ConversationState())
    await store.delete("p1")
    assert await store.get("p1") is None


def test_messages_use_whatsapp_bold_not_markdown():
    for text in (messages.menu(), messages.greeting(), messages.balance(10)):
        assert "*" in text
        assert "**" not in text  # WhatsApp não renderiza markdown
        assert "#" not in text


# --------------------------------------------------------------------------- #
# Infra de integração                                                          #
# --------------------------------------------------------------------------- #
# CPFs com dígitos verificadores VÁLIDOS: o onboarding agora valida o dígito
# (antes checava só o tamanho), então CPF inventado é recusado antes de chegar
# ao cadastro.
WA_DOCS = ["80000000086", "80000000167"]
PHONE_A = "11990000001"
PHONE_B = "11990000002"
COUPON_PREFIX = "WACONV-"
AFF_CODE = "WACONV-AFF"

REWARD_VALUE = Decimal("10.00")
REWARD_COST = 500
REWARD_ID = make_reward_id(CouponDiscountType.FIXED, REWARD_VALUE, REWARD_COST)


async def _purge(session: AsyncSession) -> None:
    await session.execute(
        delete(CouponPool).where(CouponPool.code.like(f"{COUPON_PREFIX}%"))
    )
    hashes = [hash_cpf(doc) for doc in WA_DOCS]
    ids = [
        row[0]
        for row in (
            await session.execute(
                select(Customer.id).where(Customer.cpf_hash.in_(hashes))
            )
        ).all()
    ]
    if ids:
        # Atribuições e aceites referenciam customer (FK): apagar antes.
        await session.execute(
            delete(AffiliateAttribution).where(
                AffiliateAttribution.customer_id.in_(ids)
            )
        )
        await session.execute(
            delete(TermsAcceptance).where(TermsAcceptance.customer_id.in_(ids))
        )
        await session.execute(
            delete(LedgerEntry).where(LedgerEntry.customer_id.in_(ids))
        )
        await session.execute(delete(Customer).where(Customer.id.in_(ids)))
    await session.execute(delete(Affiliate).where(Affiliate.code == AFF_CODE))
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
    try:
        yield eng
    finally:
        async with maker() as s:
            await _purge(s)
        await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine: AsyncEngine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s


@pytest_asyncio.fixture
async def store() -> InMemorySessionStore:
    return InMemorySessionStore()


# --- seed helpers ---------------------------------------------------------- #
async def _seed_customer(session, doc, *, phone=None, points=0):
    customer = await get_or_create_customer(session, doc)
    await session.commit()
    if phone is not None:
        await link_phone_to_cpf(session, phone, doc)
    if points:
        await add_entry(session, customer.id, LedgerEntryType.EARN, points, f"seed-{doc}")
        await session.commit()
    return customer


async def _seed_affiliate(
    session, *, code=AFF_CODE, active=True, points_rate=Decimal("50")
):
    affiliate = Affiliate(
        id=uuid.uuid4(),
        name="Prof. Teste",
        affiliate_type=AffiliateType.PROFESSOR,
        code=code,
        points_rate=points_rate,
        active=active,
    )
    session.add(affiliate)
    await session.commit()
    return affiliate


async def _attribution_count(session, customer_id) -> int:
    rows = (
        await session.execute(
            select(AffiliateAttribution.id).where(
                AffiliateAttribution.customer_id == customer_id
            )
        )
    ).all()
    return len(rows)


async def _seed_coupons(session, n):
    for _ in range(n):
        session.add(
            CouponPool(
                id=uuid.uuid4(),
                code=f"{COUPON_PREFIX}{uuid.uuid4().hex[:8]}",
                discount_type=CouponDiscountType.FIXED,
                discount_value=REWARD_VALUE,
                points_cost=REWARD_COST,
                status=CouponStatus.AVAILABLE,
            )
        )
    await session.commit()


# --------------------------------------------------------------------------- #
# Testes de INTEGRAÇÃO                                                         #
# --------------------------------------------------------------------------- #
async def _onboard(session_factory, store, phone: str, cpf: str) -> list[str]:
    """Percorre o onboarding completo e devolve as respostas do último passo."""
    await handle_message(phone, "oi", store, session_factory)       # saudação
    await handle_message(phone, "1", store, session_factory)        # quer participar
    await handle_message(phone, "1", store, session_factory)        # de acordo
    return await handle_message(phone, cpf, store, session_factory)  # o CPF


@pytest.mark.integration
async def test_signup_flow_of_customer_who_already_bought(
    session, session_factory, store
):
    """Quem já comprou vê o saldo dele assim que vincula o telefone."""
    await _seed_customer(session, WA_DOCS[0], points=350)

    out = await _onboard(session_factory, store, PHONE_A, WA_DOCS[0])

    # Boas-vindas mencionando o saldo que já existia + o menu.
    assert any("350" in m for m in out)
    assert any("Consultar saldo" in m for m in out)

    linked = await find_customer_by_phone(session, PHONE_A)
    assert linked is not None
    assert linked.cpf_hash == hash_cpf(WA_DOCS[0])

    # O aceite do regulamento foi GRAVADO (não só guardado na sessão).
    acceptances = (
        await session.execute(
            select(TermsAcceptance).where(
                TermsAcceptance.customer_id == linked.id
            )
        )
    ).scalars().all()
    assert len(acceptances) == 1
    assert acceptances[0].terms_version == messages.TERMS_VERSION
    assert acceptances[0].phone == PHONE_A


@pytest.mark.integration
async def test_cpf_not_in_base_is_registered_and_accumulates_from_next_purchase(
    session, session_factory, store
):
    """CPF novo agora CRIA o cadastro (era recusado antes)."""
    assert await find_customer_by_phone(session, PHONE_A) is None

    out = await _onboard(session_factory, store, PHONE_A, WA_DOCS[0])

    # Avisa que os pontos começam na próxima compra (não são retroativos).
    assert any("próxima compra" in m.lower() for m in out)

    created = await find_customer_by_phone(session, PHONE_A)
    assert created is not None
    assert created.cpf_hash == hash_cpf(WA_DOCS[0])
    # Entra com saldo zero.
    assert await get_balance(session, created.id) == 0


@pytest.mark.integration
async def test_declining_terms_does_not_create_customer(
    session, session_factory, store
):
    """Sem aceite não existe cadastro — nem cliente, nem registro de aceite."""
    await handle_message(PHONE_A, "oi", store, session_factory)
    await handle_message(PHONE_A, "1", store, session_factory)
    out = await handle_message(PHONE_A, "2", store, session_factory)

    assert any("agradece" in m.lower() for m in out)
    assert await find_customer_by_phone(session, PHONE_A) is None


@pytest.mark.integration
async def test_balance(session, session_factory, store):
    await _seed_customer(session, WA_DOCS[0], phone=PHONE_A, points=350)

    out = await handle_message(PHONE_A, "saldo", store, session_factory)
    assert len(out) == 1
    assert "350" in out[0]


@pytest.mark.integration
async def test_full_redeem_flow(session, session_factory, store):
    customer = await _seed_customer(session, WA_DOCS[0], phone=PHONE_A, points=600)
    customer_id = customer.id
    await _seed_coupons(session, 1)

    # "resgatar" -> lista numerada.
    out = await handle_message(PHONE_A, "resgatar", store, session_factory)
    assert any("1" in m and "pontos" in m for m in out)

    # escolhe "1" -> recebe código de cupom.
    out = await handle_message(PHONE_A, "1", store, session_factory)
    assert any(COUPON_PREFIX in m for m in out)

    # saldo caiu e cupom foi alocado.
    assert await get_balance(session, customer_id) == 100
    allocated = (
        await session.execute(
            select(CouponPool).where(
                CouponPool.allocated_to_customer_id == customer_id
            )
        )
    ).scalars().all()
    assert len(allocated) == 1
    assert allocated[0].status == CouponStatus.ALLOCATED


@pytest.mark.integration
async def test_redeem_without_balance(session, session_factory, store):
    customer = await _seed_customer(session, WA_DOCS[0], phone=PHONE_A, points=0)
    customer_id = customer.id
    await _seed_coupons(session, 1)

    await handle_message(PHONE_A, "resgatar", store, session_factory)
    out = await handle_message(PHONE_A, "1", store, session_factory)
    assert any("insuficiente" in m.lower() for m in out)

    # Sem efeito: saldo 0, nada alocado.
    assert await get_balance(session, customer_id) == 0
    allocated = (
        await session.execute(
            select(CouponPool.id).where(
                CouponPool.allocated_to_customer_id == customer_id
            )
        )
    ).all()
    assert len(allocated) == 0


@pytest.mark.integration
async def test_recognized_phone_goes_to_menu(session, session_factory, store):
    await _seed_customer(session, WA_DOCS[0], phone=PHONE_A, points=100)

    out = await handle_message(PHONE_A, "oi", store, session_factory)
    # Vai direto ao menu, não pede CPF.
    assert len(out) == 1
    assert "CPF" not in out[0]
    assert "Resgatar" in out[0] or "resgatar" in out[0]


@pytest.mark.integration
async def test_affiliate_code_attributes_purchase(session, session_factory, store):
    customer = await _seed_customer(session, WA_DOCS[0], phone=PHONE_A)
    customer_id = customer.id
    affiliate = await _seed_affiliate(session)
    affiliate_id = affiliate.id

    # Simula o prompt pós-compra: estado AWAITING_AFFILIATE_CODE para o telefone.
    # Compra de R$ 200; afiliado com taxa 50% -> deve ganhar 100 pontos.
    await store.set(
        normalize_phone(PHONE_A),
        ConversationState(
            step=ConversationStep.AWAITING_AFFILIATE_CODE,
            data={
                "source_reference": "WACONV-tx1",
                "points": 120,
                "amount": "200.00",
            },
        ),
    )

    out = await handle_message(PHONE_A, AFF_CODE, store, session_factory)
    assert any("indicação" in m.lower() for m in out)

    # Atribuição gravada com a compra/pontos corretos.
    attr = (
        await session.execute(
            select(AffiliateAttribution).where(
                AffiliateAttribution.source_reference == "WACONV-tx1"
            )
        )
    ).scalar_one()
    assert attr.affiliate_id == affiliate_id
    assert attr.customer_id == customer_id
    assert attr.points == 120  # pontos do cliente
    assert attr.affiliate_points == 100  # floor(200 × 50%)
    # Estado limpo após atribuir.
    assert await store.get(normalize_phone(PHONE_A)) is None


@pytest.mark.integration
async def test_affiliate_code_skip(session, session_factory, store):
    customer = await _seed_customer(session, WA_DOCS[0], phone=PHONE_A)
    customer_id = customer.id
    await _seed_affiliate(session)

    await store.set(
        normalize_phone(PHONE_A),
        ConversationState(
            step=ConversationStep.AWAITING_AFFILIATE_CODE,
            data={"source_reference": "WACONV-tx2", "points": 50},
        ),
    )

    out = await handle_message(PHONE_A, "não", store, session_factory)
    assert len(out) == 1
    # Nada atribuído e estado limpo.
    assert await _attribution_count(session, customer_id) == 0
    assert await store.get(normalize_phone(PHONE_A)) is None


@pytest.mark.integration
async def test_affiliate_code_unknown_keeps_step(session, session_factory, store):
    customer = await _seed_customer(session, WA_DOCS[0], phone=PHONE_A)
    customer_id = customer.id
    await _seed_affiliate(session)

    await store.set(
        normalize_phone(PHONE_A),
        ConversationState(
            step=ConversationStep.AWAITING_AFFILIATE_CODE,
            data={"source_reference": "WACONV-tx3", "points": 80},
        ),
    )

    out = await handle_message(PHONE_A, "CODIGO-INEXISTENTE", store, session_factory)
    assert any("não encontrei" in m.lower() for m in out)
    # Nada atribuído; mantém o passo para nova tentativa.
    assert await _attribution_count(session, customer_id) == 0
    state = await store.get(normalize_phone(PHONE_A))
    assert state is not None
    assert state.step is ConversationStep.AWAITING_AFFILIATE_CODE


@pytest.mark.integration
async def test_affiliate_yes_button_asks_for_code(session, session_factory, store):
    """Clicar "Sim" pede o código em vez de tratar o rótulo como código.

    Sem este ramo, "Sim" iria direto para a busca de afiliado, não acharia
    nada e o cliente levaria "não encontrei esse código" por ter clicado no
    botão certo — ficando preso no passo sem entender por quê.
    """
    customer = await _seed_customer(session, WA_DOCS[0], phone=PHONE_A)
    customer_id = customer.id
    await _seed_affiliate(session)

    await store.set(
        normalize_phone(PHONE_A),
        ConversationState(
            step=ConversationStep.AWAITING_AFFILIATE_CODE,
            data={
                "source_reference": "WACONV-tx4",
                "points": 90,
                "amount": "150.00",
            },
        ),
    )

    out = await handle_message(
        PHONE_A, "Sim, tenho o código", store, session_factory
    )
    assert len(out) == 1
    assert "código" in out[0].lower()
    assert "não encontrei" not in out[0].lower()

    # Nada atribuído ainda; o passo continua, agora marcado como "já pedi".
    assert await _attribution_count(session, customer_id) == 0
    state = await store.get(normalize_phone(PHONE_A))
    assert state is not None
    assert state.step is ConversationStep.AWAITING_AFFILIATE_CODE
    assert state.data["code_requested"] is True
    # Os dados da compra sobrevivem ao turno extra — sem eles não há o que
    # atribuir quando o código chegar.
    assert state.data["source_reference"] == "WACONV-tx4"
    assert state.data["points"] == 90
    assert state.data["amount"] == "150.00"


@pytest.mark.integration
async def test_affiliate_yes_then_code_attributes_purchase(
    session, session_factory, store
):
    """O fluxo completo de dois turnos: botão "Sim" -> código -> atribuição."""
    customer = await _seed_customer(session, WA_DOCS[0], phone=PHONE_A)
    customer_id = customer.id
    affiliate = await _seed_affiliate(session)
    affiliate_id = affiliate.id

    await store.set(
        normalize_phone(PHONE_A),
        ConversationState(
            step=ConversationStep.AWAITING_AFFILIATE_CODE,
            data={
                "source_reference": "WACONV-tx5",
                "points": 120,
                "amount": "200.00",
            },
        ),
    )

    await handle_message(PHONE_A, "Sim, tenho o código", store, session_factory)
    out = await handle_message(PHONE_A, AFF_CODE, store, session_factory)
    assert any("indicação" in m.lower() for m in out)

    attr = (
        await session.execute(
            select(AffiliateAttribution).where(
                AffiliateAttribution.source_reference == "WACONV-tx5"
            )
        )
    ).scalar_one()
    assert attr.affiliate_id == affiliate_id
    assert attr.customer_id == customer_id
    assert attr.points == 120
    assert attr.affiliate_points == 100  # floor(200 × 50%)
    assert await store.get(normalize_phone(PHONE_A)) is None


@pytest.mark.integration
async def test_affiliate_no_button_skips(session, session_factory, store):
    """O rótulo "Não" do botão cai no mesmo caminho do "não" digitado."""
    customer = await _seed_customer(session, WA_DOCS[0], phone=PHONE_A)
    customer_id = customer.id
    await _seed_affiliate(session)

    await store.set(
        normalize_phone(PHONE_A),
        ConversationState(
            step=ConversationStep.AWAITING_AFFILIATE_CODE,
            data={"source_reference": "WACONV-tx6", "points": 50},
        ),
    )

    out = await handle_message(PHONE_A, "Não", store, session_factory)
    assert len(out) == 1
    assert await _attribution_count(session, customer_id) == 0
    assert await store.get(normalize_phone(PHONE_A)) is None


@pytest.mark.integration
async def test_affiliate_yes_after_code_requested_is_treated_as_code(
    session, session_factory, store
):
    """Depois de pedirmos o código, "sim" volta a ser um código.

    Protege o caso improvável de um afiliado cujo código seja literalmente
    "sim": sem o `code_requested`, o cliente ficaria em laço, sempre ouvindo
    "qual é o código?".
    """
    customer = await _seed_customer(session, WA_DOCS[0], phone=PHONE_A)
    customer_id = customer.id
    await _seed_affiliate(session)

    await store.set(
        normalize_phone(PHONE_A),
        ConversationState(
            step=ConversationStep.AWAITING_AFFILIATE_CODE,
            data={
                "source_reference": "WACONV-tx7",
                "points": 70,
                "amount": "100.00",
                "code_requested": True,
            },
        ),
    )

    out = await handle_message(PHONE_A, "sim", store, session_factory)
    # Tratado como código (inexistente), não como novo "sim".
    assert any("não encontrei" in m.lower() for m in out)
    assert await _attribution_count(session, customer_id) == 0
    state = await store.get(normalize_phone(PHONE_A))
    assert state is not None
    assert state.step is ConversationStep.AWAITING_AFFILIATE_CODE


@pytest.mark.integration
async def test_conversational_state_one_is_balance_or_reward(
    session, session_factory, store
):
    customer = await _seed_customer(session, WA_DOCS[0], phone=PHONE_A, points=600)
    customer_id = customer.id
    await _seed_coupons(session, 1)

    # Fora do passo de escolha: "1" é atalho de SALDO.
    out = await handle_message(PHONE_A, "1", store, session_factory)
    assert "600" in out[0]  # saldo, não resgate
    assert await get_balance(session, customer_id) == 600  # nada debitado

    # Entra no passo de escolha de recompensa.
    await handle_message(PHONE_A, "resgatar", store, session_factory)
    # Agora "1" é a ESCOLHA da recompensa -> resgata.
    out = await handle_message(PHONE_A, "1", store, session_factory)
    assert any(COUPON_PREFIX in m for m in out)
    assert await get_balance(session, customer_id) == 100  # debitou 500
