"""Tabela admin_users (usuários do painel administrativo).

Revision ID: 0004_admin_users
Revises: 0003_scoring_rules
Create Date: 2026-06-05

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004_admin_users"
down_revision: Union[str, None] = "0003_scoring_rules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_admin_users_username"), "admin_users", ["username"], unique=True
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_admin_users_username"), table_name="admin_users")
    op.drop_table("admin_users")
