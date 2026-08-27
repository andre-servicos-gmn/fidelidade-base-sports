"""Índice único parcial em customers.phone (um telefone -> um cliente).

Revision ID: 0002_phone_unique
Revises: 0001_initial
Create Date: 2026-06-05

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_phone_unique"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Índice PARCIAL: a unicidade só vale para telefones não-nulos, permitindo
    # muitos clientes ainda sem WhatsApp vinculado (phone IS NULL).
    op.create_index(
        "uq_customers_phone",
        "customers",
        ["phone"],
        unique=True,
        postgresql_where=sa.text("phone IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_customers_phone", table_name="customers")
