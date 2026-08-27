"""Tabela terms_acceptances (aceite do regulamento do Base Club).

Guarda a PROVA do aceite: quem, quando, de qual telefone e qual versão do
texto. O estado da conversa é efêmero e não serve como evidência.

Revision ID: 0009_terms_acceptances
Revises: 0008_affiliate_points_rate
Create Date: 2026-08-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0009_terms_acceptances"
down_revision: Union[str, None] = "0008_affiliate_points_rate"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "terms_acceptances",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("phone", sa.String(), nullable=False),
        sa.Column("terms_version", sa.String(), nullable=False),
        sa.Column(
            "accepted_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_terms_acceptances_customer_id"),
        "terms_acceptances",
        ["customer_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_terms_acceptances_customer_id"),
        table_name="terms_acceptances",
    )
    op.drop_table("terms_acceptances")
