"""Tabela affiliate_attributions (atribuição de compras a afiliados).

Revision ID: 0007_affiliate_attributions
Revises: 0006_affiliates
Create Date: 2026-06-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0007_affiliate_attributions"
down_revision: Union[str, None] = "0006_affiliates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "affiliate_attributions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "affiliate_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_reference", sa.String(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["affiliate_id"], ["affiliates.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_reference", name="uq_affiliate_attr_source_reference"
        ),
    )
    op.create_index(
        op.f("ix_affiliate_attributions_affiliate_id"),
        "affiliate_attributions",
        ["affiliate_id"],
    )
    op.create_index(
        op.f("ix_affiliate_attributions_customer_id"),
        "affiliate_attributions",
        ["customer_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_affiliate_attributions_customer_id"),
        table_name="affiliate_attributions",
    )
    op.drop_index(
        op.f("ix_affiliate_attributions_affiliate_id"),
        table_name="affiliate_attributions",
    )
    op.drop_table("affiliate_attributions")
