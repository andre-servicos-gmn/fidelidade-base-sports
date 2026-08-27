"""Pontos do afiliado: taxa por professor + valor/pontos na atribuição.

Adiciona:
  - affiliates.points_rate (percentual; default 0)
  - affiliate_attributions.amount (valor da compra em R$; default 0)
  - affiliate_attributions.affiliate_points (pontos do afiliado; default 0)

Revision ID: 0008_affiliate_points_rate
Revises: 0007_affiliate_attributions
Create Date: 2026-06-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_affiliate_points_rate"
down_revision: Union[str, None] = "0007_affiliate_attributions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "affiliates",
        sa.Column(
            "points_rate",
            sa.Numeric(6, 2),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "affiliate_attributions",
        sa.Column(
            "amount",
            sa.Numeric(10, 2),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "affiliate_attributions",
        sa.Column(
            "affiliate_points",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("affiliate_attributions", "affiliate_points")
    op.drop_column("affiliate_attributions", "amount")
    op.drop_column("affiliates", "points_rate")
