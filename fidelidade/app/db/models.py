"""Modelos SQLAlchemy 2.0 (estilo `Mapped` / `mapped_column`).

Nada de CPF em texto plano: o `Customer` guarda apenas o HMAC do CPF
(`cpf_hash`) e uma versão mascarada para exibição (`cpf_masked`).

O `LedgerEntry` é APPEND-ONLY: jamais faça update/delete. Cada lançamento
encadeia o hash do anterior (`previous_hash`), formando uma cadeia
verificável (ver `app.domain.ledger`).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum as PyEnum

from sqlalchemy import (
    Boolean,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.scoring import RuleType


# --------------------------------------------------------------------------- #
# Enums                                                                        #
# --------------------------------------------------------------------------- #
class LedgerEntryType(str, PyEnum):
    """Tipo de lançamento no ledger."""

    EARN = "EARN"
    REDEEM = "REDEEM"
    EXPIRE = "EXPIRE"
    ADJUST = "ADJUST"


class CouponDiscountType(str, PyEnum):
    """Tipo de desconto de um cupom."""

    FIXED = "FIXED"
    PERCENTAGE = "PERCENTAGE"


class CouponStatus(str, PyEnum):
    """Ciclo de vida de um cupom no pool."""

    AVAILABLE = "AVAILABLE"
    ALLOCATED = "ALLOCATED"
    USED = "USED"
    EXPIRED = "EXPIRED"


class AffiliateType(str, PyEnum):
    """Tipo de afiliado (parceiro que divulga um código)."""

    PROFESSOR = "PROFESSOR"
    INFLUENCER = "INFLUENCER"


# Reaproveitado por colunas com timezone.
_TZ = TIMESTAMP(timezone=True)


# --------------------------------------------------------------------------- #
# Models                                                                       #
# --------------------------------------------------------------------------- #
class Customer(Base):
    """Cliente do programa de fidelidade.

    Identificado pelo hash determinístico do CPF (`cpf_hash`), o que permite
    buscar por CPF sem nunca armazenar o CPF cru.
    """

    __tablename__ = "customers"
    __table_args__ = (
        # Índice único PARCIAL: um telefone só pode vincular a um cliente, mas
        # vários clientes podem ter phone NULL (ainda não vincularam WhatsApp).
        # No Postgres, UNIQUE comum bloquearia múltiplos NULLs; por isso o
        # índice parcial com WHERE phone IS NOT NULL.
        Index(
            "uq_customers_phone",
            "phone",
            unique=True,
            postgresql_where=text("phone IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    cpf_hash: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )
    cpf_masked: Mapped[str] = mapped_column(String, nullable=False)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        _TZ, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        _TZ, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class LedgerEntry(Base):
    """Lançamento no ledger de pontos (APPEND-ONLY, hash-encadeado)."""

    __tablename__ = "ledger_entries"
    __table_args__ = (
        # Garante a ordem/posição única de cada lançamento na cadeia do cliente.
        UniqueConstraint(
            "customer_id", "sequence", name="uq_ledger_customer_sequence"
        ),
        # Idempotência da ingestão: uma transação TouchPay (source_reference)
        # só pode virar um lançamento. No Postgres, UNIQUE permite múltiplos
        # NULLs, então a restrição só vale quando source_reference é não-nulo.
        UniqueConstraint(
            "source_reference", name="uq_ledger_source_reference"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id"),
        index=True,
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_type: Mapped[LedgerEntryType] = mapped_column(
        Enum(LedgerEntryType, name="ledger_entry_type"), nullable=False
    )
    # Positivo para EARN/ADJUST(+); negativo para REDEEM/EXPIRE.
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    previous_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    entry_hash: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        _TZ, server_default=func.now(), nullable=False
    )


class CouponPool(Base):
    """Pool de cupons pré-gerados no TouchPay e cadastrados aqui."""

    __tablename__ = "coupon_pool"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    discount_type: Mapped[CouponDiscountType] = mapped_column(
        Enum(CouponDiscountType, name="coupon_discount_type"), nullable=False
    )
    discount_value: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False
    )
    points_cost: Mapped[int] = mapped_column(Integer, nullable=False)
    # Pedido mínimo (R$) para o cupom poder ser usado no totem. NULO = sem
    # mínimo. É INFORMATIVO do nosso lado: reflete o mínimo configurado no cupom
    # real do TouchPay. Nosso sistema não valida a compra (quem valida é o
    # totem); guardamos para COMUNICAR a condição ao cliente antes do resgate.
    min_order_value: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    status: Mapped[CouponStatus] = mapped_column(
        Enum(CouponStatus, name="coupon_status"),
        default=CouponStatus.AVAILABLE,
        nullable=False,
    )
    allocated_to_customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id"), nullable=True
    )
    allocated_at: Mapped[datetime | None] = mapped_column(_TZ, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(_TZ, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        _TZ, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        _TZ, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AdminUser(Base):
    """Usuário do painel administrativo. Senha NUNCA em texto plano."""

    __tablename__ = "admin_users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    username: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        _TZ, server_default=func.now(), nullable=False
    )


class ScoringRule(Base):
    """Regra de pontuação persistida (consumida pelo motor da Fase 2).

    Espelha o `Rule` do domínio (`app.domain.scoring.Rule`). `params` é um JSONB
    que guarda os parâmetros específicos do tipo (ex: {"points_per_real": 1.0},
    {"category": "Raquetes", "multiplier": 2.0}). O `rule_service` converte cada
    linha para o `Rule` do domínio via `Rule.from_dict`.
    """

    __tablename__ = "scoring_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    rule_type: Mapped[RuleType] = mapped_column(
        Enum(RuleType, name="scoring_rule_type"), nullable=False
    )
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(_TZ, nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(_TZ, nullable=True)
    params: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        _TZ, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        _TZ, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Affiliate(Base):
    """Afiliado (professor/influencer) dono de um código de divulgação.

    O `code` é o identificador que o cliente informa para que o cadastro/venda
    seja atribuído a este afiliado. É único e guardado SEMPRE em MAIÚSCULAS
    (normalizado na camada de schema/serviço), o que torna a busca por código
    case-insensitive na prática.

    Nesta fase o afiliado é só CADASTRO/gestão: a atribuição cliente -> afiliado
    e as métricas chegam na fase seguinte (sem alterar este schema).
    """

    __tablename__ = "affiliates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    affiliate_type: Mapped[AffiliateType] = mapped_column(
        Enum(AffiliateType, name="affiliate_type"), nullable=False
    )
    code: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )
    # Taxa de pontos que o AFILIADO ganha por compra atribuída, em PERCENTUAL
    # (100.00 = 100%). Base: 1 real = 1 ponto, então pontos do afiliado =
    # floor(valor_da_compra_em_reais × points_rate/100). Default 0 = não ganha
    # até ser configurado no painel.
    points_rate: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), nullable=False, server_default=text("0")
    )
    # Contato livre (telefone/e-mail/@) e anotações — ambos opcionais.
    contact: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        _TZ, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        _TZ, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TermsAcceptance(Base):
    """Aceite do regulamento do Base Club — PROVA, não estado de conversa.

    Por que é tabela e não passo de sessão: o estado conversacional vive em
    memória (ou Redis) e expira em minutos. Um aceite que morre com a sessão não
    serve como evidência de nada. Aqui fica quem aceitou, quando, de qual
    telefone e **qual versão** do texto — sem a versão, provar o aceite não
    prova o que foi aceito.

    APPEND-ONLY por intenção: nunca atualize nem apague uma linha. Se o
    regulamento mudar, o cliente aceita de novo e nasce uma linha nova, com a
    versão nova. O histórico é o registro.
    """

    __tablename__ = "terms_acceptances"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id"),
        index=True,
        nullable=False,
    )
    # Telefone (canônico) de onde veio o aceite. Redundante com o do cliente de
    # propósito: se o vínculo mudar depois, este registro continua fiel ao fato.
    phone: Mapped[str] = mapped_column(String, nullable=False)
    terms_version: Mapped[str] = mapped_column(String, nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(
        _TZ, server_default=func.now(), nullable=False
    )


class AffiliateAttribution(Base):
    """Atribuição de UMA compra a um afiliado (rastreamento por compra).

    O cliente informa o código de afiliado no WhatsApp logo após a compra; cada
    linha aqui liga a transação (`source_reference`, o uuid da TouchPay) ao
    afiliado. NÃO mexemos no ledger (append-only, hash-encadeado): a atribuição
    vive nesta tabela à parte.

    `source_reference` é ÚNICO: uma compra é atribuída no máximo uma vez (mesma
    ideia da idempotência de `LedgerEntry.source_reference`).
    """

    __tablename__ = "affiliate_attributions"
    __table_args__ = (
        UniqueConstraint(
            "source_reference", name="uq_affiliate_attr_source_reference"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    affiliate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("affiliates.id"),
        index=True,
        nullable=False,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id"),
        index=True,
        nullable=False,
    )
    source_reference: Mapped[str] = mapped_column(String, nullable=False)
    # Pontos do CLIENTE gerados pela compra (métrica). NÃO confundir com os
    # pontos do afiliado.
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    # Valor da compra em reais (base de cálculo) e pontos do AFILIADO, gravados
    # como snapshot no momento da atribuição (mudar a taxa não altera o histórico).
    amount: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, server_default=text("0")
    )
    affiliate_points: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        _TZ, server_default=func.now(), nullable=False
    )
