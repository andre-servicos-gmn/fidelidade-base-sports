"""Adiciona coupon_pool.min_order_value (pedido mínimo, informativo).

Revision ID: 0005_coupon_min_order
Revises: 0004_admin_users
Create Date: 2026-06-05

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_coupon_min_order"
down_revision: Union[str, None] = "0004_admin_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable: cupons antigos (sem mínimo) continuam válidos. NULO = sem mínimo.
    op.add_column(
        "coupon_pool",
        sa.Column("min_order_value", sa.Numeric(10, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("coupon_pool", "min_order_value")
