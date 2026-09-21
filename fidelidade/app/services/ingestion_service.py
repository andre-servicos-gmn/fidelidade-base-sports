"""Serviço de ingestão: orquestra TouchPay -> motor de pontos -> ledger.

Conecta três peças já existentes:
  - `TouchPayClient` (lê transações + catálogo),
  - `calculate_points` (motor de pontuação, domínio puro),
  - o ledger (`add_entry`, com idempotência via constraint única).

IDEMPOTÊNCIA é o princípio inviolável: o polling relê transações de propósito
(janelas sobrepostas). Cada transação só gera pontos UMA vez, garantido pela
constraint única em `LedgerEntry.source_reference` (o uuid da transação).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.scoring import Rule, calculate_points
from app.db.models import LedgerEntryType
from app.integrations.touchpay.client import TouchPayClient
from app.mappers.transaction_mapper import to_scoring_context
from app.services.affiliate_service import create_affiliate_question
from app.services.customer_service import get_or_create_customer
from app.services.ledger_service import add_entry, is_first_purchase
from app.services.rule_service import get_active_rules
from app.whatsapp.affiliate_prompt import AffiliatePrompt


@dataclass
class IngestionReport:
    """Resumo de uma rodada de ingestão."""

    total_read: int = 0
    credited: int = 0
    already_processed: int = 0
    skipped_no_cpf: int = 0
    errors: int = 0
    total_points_credited: int = 0
    # Compras recém-creditadas de clientes com telefone: viram a pergunta de
    # afiliado. Enviadas por `dispatch_affiliate_prompts` (não aqui).
    affiliate_prompts: list[AffiliatePrompt] = field(default_factory=list)


async def _load_product_categories(
    touchpay_client: TouchPayClient,
) -> dict[int, str]:
    """Carrega o catálogo e monta o cache product_id -> categoria."""
    categories: dict[int, str] = {}
    page = 1
    while True:
        products = await touchpay_client.list_products(page=page, page_size=1000)
        for product in products:
            product_id = product.get("productId")
            category = product.get("categoryName")
            if product_id is not None and category is not None:
                categories[int(product_id)] = category
        # O mock devolve lista simples; paramos quando uma página vier vazia.
        if not products or len(products) < 1000:
            break
        page += 1
    return categories


async def ingest_transactions(
    session: AsyncSession,
    touchpay_client: TouchPayClient,
    min_date: datetime,
    max_date: datetime,
    rules: list[Rule] | None = None,
    page_size: int = 50,
) -> IngestionReport:
    """Ingere as transações da janela [min_date, max_date] e credita pontos.

    Regras
    ------
    Se `rules` for None (padrão em produção), carrega as regras VIGENTES do
    banco via `get_active_rules` — assim mudanças feitas no painel entram em
    vigor sem mexer em código. Os testes podem passar `rules` para sobrepor a
    leitura do banco e ficarem determinísticos.

    Estratégia transacional
    ------------------------
    Fazemos **commit por transação processada**. Assim, uma falha numa
    transação não desfaz as anteriores (já commitadas). A captura de
    duplicidade (idempotência) acontece num SAVEPOINT dentro de `add_entry`,
    então uma transação repetida NÃO aborta o lote — apenas é contada como
    "já processada".

    Paginação
    ---------
    Percorremos TODAS as páginas enquanto `hasNextPage` for True. NÃO paramos
    na primeira página (este é um ponto crítico: parar cedo perderia vendas).
    """
    if rules is None:
        rules = await get_active_rules(session)

    report = IngestionReport()
    categories = await _load_product_categories(touchpay_client)

    page = 1
    while True:
        paginated = await touchpay_client.get_transactions(
            min_date=min_date,
            max_date=max_date,
            page=page,
            page_size=page_size,
        )
        transactions = paginated.items or []

        for transaction in transactions:
            report.total_read += 1

            cpf = (
                transaction.userInfo.document
                if transaction.userInfo is not None
                else None
            )
            if not cpf or not cpf.strip():
                report.skipped_no_cpf += 1
                continue

            try:
                customer = await get_or_create_customer(
                    session,
                    cpf,
                    phone=(
                        transaction.userInfo.phoneNumber
                        if transaction.userInfo
                        else None
                    ),
                )

                context = to_scoring_context(transaction, categories)
                result = calculate_points(context, rules)

                if result.total_points > 0:
                    n_items = len(transaction.items or [])
                    entry = await add_entry(
                        session,
                        customer_id=customer.id,
                        entry_type=LedgerEntryType.EARN,
                        points=result.total_points,
                        source_reference=transaction.uuid,
                        description=(
                            f"Compra PDV {transaction.pointOfSaleId} "
                            f"- {n_items} itens"
                        ),
                    )
                    if entry is None:
                        report.already_processed += 1
                    else:
                        report.credited += 1
                        report.total_points_credited += result.total_points
                        # Cliente com telefone -> gera a pergunta de afiliado
                        # para ESTA compra (enviada depois, fora da ingestão).
                        #
                        # TRAVA DE COMISSÃO: só perguntamos na PRIMEIRA compra
                        # do CPF. O afiliado é remunerado por trazer o cliente,
                        # não por cada compra dele — então perguntar de novo
                        # seria pedir um dado que não pode virar comissão (e,
                        # na API oficial do WhatsApp, uma mensagem paga à toa).
                        if customer.phone and await is_first_purchase(
                            session, customer.id, transaction.uuid
                        ):
                            # A pergunta fica registrada no banco, na mesma
                            # transação do crédito: é lá que a resposta do
                            # cliente vai procurá-la, mesmo horas depois.
                            await create_affiliate_question(
                                session,
                                customer_id=customer.id,
                                source_reference=transaction.uuid,
                                points=result.total_points,
                                amount=Decimal(str(transaction.totalPrice)),
                            )
                            report.affiliate_prompts.append(
                                AffiliatePrompt(
                                    phone=customer.phone,
                                    points=result.total_points,
                                    source_reference=transaction.uuid,
                                    amount=Decimal(str(transaction.totalPrice)),
                                )
                            )

                # Commit por transação: torna durável o que já foi processado.
                await session.commit()
            except Exception:
                # Falha inesperada nesta transação: desfaz só o trabalho dela
                # (as anteriores já foram commitadas) e segue o lote.
                await session.rollback()
                report.errors += 1

        if not paginated.hasNextPage:
            break
        page += 1

    return report
