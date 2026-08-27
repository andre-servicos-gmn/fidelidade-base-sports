"""Tabela scoring_rules (regras de pontuação persistidas).

Revision ID: 0003_scoring_rules
Revises: 0002_phone_unique
Create Date: 2026-06-05

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003_scoring_rules"
down_revision: Union[str, None] = "0002_phone_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

scoring_rule_type = sa.Enum(
    "BASE",
    "CATEGORY_MULTIPLIER",
    "PRODUCT_MULTIPLIER",
    "CATEGORY_BONUS_PERCENT",
    name="scoring_rule_type",
)


def upgrade() -> None:
    op.create_table(
        "scoring_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("rule_type", scoring_rule_type, nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("valid_from", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("valid_until", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
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


def downgrade() -> None:
    op.drop_table("scoring_rules")
    scoring_rule_type.drop(op.get_bind(), checkfirst=True)
