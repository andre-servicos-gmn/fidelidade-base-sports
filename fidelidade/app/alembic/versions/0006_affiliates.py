"""Tabela affiliates (afiliados: professores/influencers e seus códigos).

Revision ID: 0006_affiliates
Revises: 0005_coupon_min_order
Create Date: 2026-06-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0006_affiliates"
down_revision: Union[str, None] = "0005_coupon_min_order"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

affiliate_type = sa.Enum("PROFESSOR", "INFLUENCER", name="affiliate_type")


def upgrade() -> None:
    op.create_table(
        "affiliates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("affiliate_type", affiliate_type, nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("contact", sa.String(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_affiliates_code"), "affiliates", ["code"], unique=True
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_affiliates_code"), table_name="affiliates")
    op.drop_table("affiliates")
    affiliate_type.drop(op.get_bind(), checkfirst=True)
