"""Testes da camada de ingestão.

- Mapper e lógica pura: testes de unidade (sem banco).
- Idempotência, saldo, cadeia de hash e paginação: @pytest.mark.integration
  (exigem Supabase/Postgres real).
"""

from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db.models import Customer, LedgerEntry, LedgerEntryType
from app.domain.ledger import verify_chain
from app.domain.scoring import Rule, RuleType
from app.integrations.touchpay.client import TouchPayClient
from app.integrations.touchpay.mock_client import MockTouchPayClient
from app.integrations.touchpay.schemas import (
    PaginatedTransactions,
    Transaction,
    TransactionItem,
    UserInfo,
)
from app.mappers.transaction_mapper import to_scoring_context
from app.domain.security import hash_cpf
from app.services.customer_service import get_or_create_customer
from app.services.identity_service import find_customer_by_phone, normalize_phone
from app.services.ingestion_service import ingest_transactions
from app.services.ledger_service import add_entry, get_balance
from app.whatsapp.affiliate_prompt import dispatch_affiliate_prompts
from app.whatsapp.evolution.sender import MockMessageSender
from app.whatsapp.session_store import ConversationStep, InMemorySessionStore

MIN_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
MAX_DATE = datetime(2026, 6, 1, tzinfo=timezone.utc)

# CPFs usados pelo MockTouchPayClient + os usados só nos testes.
MOCK_DOCS = ["11111111111", "22222222222", "33333333333", "44444444444"]
EXTRA_TEST_DOCS = ["99999999999"]
ALL_TEST_DOCS = MOCK_DOCS + EXTRA_TEST_DOCS


def _base_rules() -> list[Rule]:
    return [
        Rule(
            id="base",
            name="1 ponto por real",
            rule_type=RuleType.BASE,
            params={"points_per_real": 1.0},
        )
    ]


# --------------------------------------------------------------------------- #
# Testes de UNIDADE (sem banco)                                                #
# --------------------------------------------------------------------------- #
def test_mapper_enriches_categories_and_computes_totals():
    tx = Transaction(
        uuid="t1",
        totalPrice=350.0,
        paymentAmount=350.0,
        pointOfSaleId=1,
        date=datetime(2026, 3, 1, tzinfo=timezone.utc),
        userInfo=UserInfo(document="11111111111"),
        items=[
            TransactionItem(productId=1, quantity=1, price=300.0),
            TransactionItem(productId=3, quantity=2, price=25.0),
        ],
    )
    categories = {1: "Raquetes", 3: "Acessorios"}

    ctx = to_scoring_context(tx, categories)

    assert ctx.total_amount == 350.0
    assert ctx.transaction_date == tx.date
    assert len(ctx.items) == 2
    assert ctx.items[0].category == "Raquetes"
    assert ctx.items[0].total_price == 300.0
    assert ctx.items[1].total_price == 50.0  # 25.0 * 2


def test_mapper_missing_category_becomes_none():
    tx = Transaction(
        uuid="t2",
        totalPrice=10.0,
        paymentAmount=10.0,
        pointOfSaleId=1,
        date=datetime(2026, 3, 1, tzinfo=timezone.utc),
        userInfo=UserInfo(document="11111111111"),
        items=[TransactionItem(productId=999, quantity=1, price=10.0)],
    )
    ctx = to_scoring_context(tx, {1: "Raquetes"})
    assert ctx.items[0].category is None


def test_mapper_handles_no_items():
    tx = Transaction(
        uuid="t3",
        totalPrice=0.0,
        paymentAmount=0.0,
        pointOfSaleId=1,
        date=datetime(2026, 3, 1, tzinfo=timezone.utc),
        userInfo=None,
        items=None,
    )
    ctx = to_scoring_context(tx, {})
    assert ctx.items == []


# --------------------------------------------------------------------------- #
# Infra dos testes de INTEGRAÇÃO                                               #
# --------------------------------------------------------------------------- #
async def _purge_test_data(session: AsyncSession) -> None:
    """Remove clientes de teste (e seus lançamentos) para isolar as rodadas."""
    hashes = [hash_cpf(doc) for doc in ALL_TEST_DOCS]
    ids = [
        row[0]
        for row in (
            await session.execute(
                select(Customer.id).where(Customer.cpf_hash.in_(hashes))
            )
        ).all()
    ]
    if ids:
        await session.execute(
            delete(LedgerEntry).where(LedgerEntry.customer_id.in_(ids))
        )
        await session.execute(delete(Customer).where(Customer.id.in_(ids)))
    await session.commit()


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    settings = get_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL não configurada.")

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        await _purge_test_data(s)  # começa limpo
        try:
            yield s
        finally:
            await _purge_test_data(s)  # encerra limpo
    await engine.dispose()


async def _count_entries(session: AsyncSession, docs: list[str]) -> int:
    hashes = [hash_cpf(doc) for doc in docs]
    ids = [
        row[0]
        for row in (
            await session.execute(
                select(Customer.id).where(Customer.cpf_hash.in_(hashes))
            )
        ).all()
    ]
    if not ids:
        return 0
    rows = (
        await session.execute(
            select(LedgerEntry.id).where(LedgerEntry.customer_id.in_(ids))
        )
    ).all()
    return len(rows)


# --------------------------------------------------------------------------- #
# Testes de INTEGRAÇÃO                                                         #
# --------------------------------------------------------------------------- #
@pytest.mark.integration
async def test_customer_phone_is_stored_canonical(session: AsyncSession):
    """REGRESSÃO: o telefone precisa ser gravado no formato canônico.

    A TouchPay entrega o telefone com código de país ("+5511987654321"), mas
    `find_customer_by_phone` — usada pelo webhook — compara com a forma canônica
    ("11987654321"). Gravar o valor cru cria um cliente que acumula pontos
    normalmente e que o bot NUNCA reconhece: ele trata como desconhecido e pede
    o CPF, sem erro em log nenhum. Falha silenciosa, difícil de achar em produção.
    """
    raw_phone = "+55 (11) 98765-4321"
    customer = await get_or_create_customer(
        session, EXTRA_TEST_DOCS[0], phone=raw_phone
    )
    await session.commit()

    assert customer.phone == "11987654321"

    # A busca do webhook precisa achá-lo, venha o número em que formato vier.
    for lookup in (raw_phone, "5511987654321", "11987654321"):
        found = await find_customer_by_phone(session, lookup)
        assert found is not None, f"não encontrou com {lookup!r}"
        assert found.id == customer.id


@pytest.mark.integration
async def test_ingestion_is_idempotent(session: AsyncSession):
    client = MockTouchPayClient()
    rules = _base_rules()

    first = await ingest_transactions(session, client, MIN_DATE, MAX_DATE, rules)
    count_after_first = await _count_entries(session, MOCK_DOCS)

    assert first.credited == 4
    assert first.already_processed == 0
    assert first.total_points_credited == 680  # 350 + 90 + 180 + 60
    assert count_after_first == 4

    second = await ingest_transactions(session, client, MIN_DATE, MAX_DATE, rules)
    count_after_second = await _count_entries(session, MOCK_DOCS)

    # Segunda passada: ninguém creditado, todas já processadas.
    assert second.credited == 0
    assert second.already_processed == 4
    assert second.total_points_credited == 0
    # O número de lançamentos NÃO mudou.
    assert count_after_second == count_after_first == 4


@pytest.mark.integration
async def test_balance_matches_expected_points(session: AsyncSession):
    client = MockTouchPayClient()
    await ingest_transactions(session, client, MIN_DATE, MAX_DATE, _base_rules())

    customer = (
        await session.execute(
            select(Customer).where(
                Customer.cpf_hash == hash_cpf("11111111111")
            )
        )
    ).scalar_one()

    # Itens do cliente 1: 300 + (25*2) = 350 pontos (base 1/real).
    assert await get_balance(session, customer.id) == 350


@pytest.mark.integration
async def test_hash_chain_stays_valid_across_credits(session: AsyncSession):
    from app.services.customer_service import get_or_create_customer

    customer = await get_or_create_customer(session, "99999999999")
    await session.commit()

    await add_entry(session, customer.id, LedgerEntryType.EARN, 100, "ref-a")
    await session.commit()
    await add_entry(session, customer.id, LedgerEntryType.REDEEM, -30, "ref-b")
    await session.commit()
    await add_entry(session, customer.id, LedgerEntryType.ADJUST, 50, "ref-c")
    await session.commit()

    entries = (
        await session.execute(
            select(LedgerEntry)
            .where(LedgerEntry.customer_id == customer.id)
            .order_by(LedgerEntry.sequence.asc())
        )
    ).scalars().all()

    assert len(entries) == 3
    assert [e.balance_after for e in entries] == [100, 70, 120]
    assert verify_chain(list(entries)) is True


@pytest.mark.integration
async def test_transaction_without_cpf_is_skipped(session: AsyncSession):
    class _NoCpfClient(MockTouchPayClient):
        async def get_transactions(
            self, min_date, max_date, page=1, page_size=50,
            cpf=None, point_of_sale_id=None,
        ) -> PaginatedTransactions:
            tx = Transaction(
                uuid="nocpf-tx",
                totalPrice=10.0,
                paymentAmount=10.0,
                pointOfSaleId=9,
                date=min_date,
                userInfo=UserInfo(document=None),
                items=[TransactionItem(productId=1, quantity=1, price=10.0)],
            )
            return PaginatedTransactions(
                items=[tx],
                pageIndex=1,
                totalPages=1,
                totalItems=1,
                pageSize=page_size,
                hasPreviousPage=False,
                hasNextPage=False,
            )

    report = await ingest_transactions(
        session, _NoCpfClient(), MIN_DATE, MAX_DATE, _base_rules()
    )

    assert report.total_read == 1
    assert report.skipped_no_cpf == 1
    assert report.credited == 0


@pytest.mark.integration
async def test_ingestion_collects_and_dispatches_affiliate_prompts(
    session: AsyncSession,
):
    client = MockTouchPayClient()
    report = await ingest_transactions(
        session, client, MIN_DATE, MAX_DATE, _base_rules()
    )

    # 4 compras creditadas, todas de clientes novos COM telefone -> 4 prompts.
    assert report.credited == 4
    assert len(report.affiliate_prompts) == 4

    prompt1 = next(
        p
        for p in report.affiliate_prompts
        if p.source_reference == "11111111-1111-1111-1111-111111111111"
    )
    assert prompt1.points == 350  # 300 + 25*2 (base 1/real)
    assert float(prompt1.amount) == 350.0  # valor da compra (R$)

    # Dispatch grava o estado e envia via MockMessageSender.
    sender = MockMessageSender()
    store = InMemorySessionStore()
    sent = await dispatch_affiliate_prompts(
        report.affiliate_prompts, sender, store
    )
    assert sent == 4
    assert len(sender.sent) == 4

    phone_n = normalize_phone(prompt1.phone)
    state = await store.get(phone_n)
    assert state is not None
    assert state.step is ConversationStep.AWAITING_AFFILIATE_CODE
    assert state.data["source_reference"] == prompt1.source_reference
    assert state.data["points"] == 350
    assert state.data["amount"] == "350.0"


@pytest.mark.integration
async def test_all_pages_are_processed(session: AsyncSession):
    # page_size=2 sobre as 4 transações do mock -> 2 páginas.
    # Prova que NÃO paramos na primeira página.
    client = MockTouchPayClient()
    report = await ingest_transactions(
        session, client, MIN_DATE, MAX_DATE, _base_rules(), page_size=2
    )

    assert report.total_read == 4
    assert report.credited == 4
    assert await _count_entries(session, MOCK_DOCS) == 4
