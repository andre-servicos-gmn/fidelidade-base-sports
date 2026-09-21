"""Tabela affiliate_questions (pergunta de indicação pós-compra).

A pergunta "veio por indicação?" passa a viver no banco, não no estado da
conversa: o cliente responde ao template horas depois, e o estado em memória
expirava (ou sumia no deploy) antes disso.

Aditiva: o código antigo ignora a tabela, então dá para aplicar ANTES do
deploy do código novo.

Revision ID: 0010_affiliate_questions
Revises: 0009_terms_acceptances
Create Date: 2026-09-21

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0010_affiliate_questions"
down_revision: Union[str, None] = "0009_terms_acceptances"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

affiliate_question_status = sa.Enum(
    "PENDING", "ATTRIBUTED", "DECLINED", name="affiliate_question_status"
)


def upgrade() -> None:
    op.create_table(
        "affiliate_questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_reference", sa.String(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("status", affiliate_question_status, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("answered_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_reference", name="uq_affiliate_question_source_reference"
        ),
    )
    op.create_index(
        op.f("ix_affiliate_questions_customer_id"),
        "affiliate_questions",
        ["customer_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_affiliate_questions_customer_id"),
        table_name="affiliate_questions",
    )
    op.drop_table("affiliate_questions")
    affiliate_question_status.drop(op.get_bind(), checkfirst=True)
