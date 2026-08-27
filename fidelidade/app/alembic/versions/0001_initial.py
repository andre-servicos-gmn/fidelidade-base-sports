"""Migration inicial: customers, ledger_entries, coupon_pool.

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-05

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ledger_entry_type = sa.Enum(
    "EARN", "REDEEM", "EXPIRE", "ADJUST", name="ledger_entry_type"
)
coupon_discount_type = sa.Enum(
    "FIXED", "PERCENTAGE", name="coupon_discount_type"
)
coupon_status = sa.Enum(
    "AVAILABLE", "ALLOCATED", "USED", "EXPIRED", name="coupon_status"
)


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cpf_hash", sa.String(), nullable=False),
        sa.Column("cpf_masked", sa.String(), nullable=False),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=True),
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
        op.f("ix_customers_cpf_hash"), "customers", ["cpf_hash"], unique=True
    )

    op.create_table(
        "ledger_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "customer_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("entry_type", ledger_entry_type, nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column("source_reference", sa.String(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("previous_hash", sa.String(), nullable=True),
        sa.Column("entry_hash", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "customer_id", "sequence", name="uq_ledger_customer_sequence"
        ),
        sa.UniqueConstraint(
            "source_reference", name="uq_ledger_source_reference"
        ),
    )
    op.create_index(
        op.f("ix_ledger_entries_customer_id"),
        "ledger_entries",
        ["customer_id"],
        unique=False,
    )

    op.create_table(
        "coupon_pool",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("discount_type", coupon_discount_type, nullable=False),
        sa.Column("discount_value", sa.Numeric(10, 2), nullable=False),
        sa.Column("points_cost", sa.Integer(), nullable=False),
        sa.Column("status", coupon_status, nullable=False),
        sa.Column(
            "allocated_to_customer_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("allocated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["allocated_to_customer_id"], ["customers.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )


def downgrade() -> None:
    op.drop_table("coupon_pool")
    op.drop_index(
        op.f("ix_ledger_entries_customer_id"), table_name="ledger_entries"
    )
    op.drop_table("ledger_entries")
    op.drop_index(op.f("ix_customers_cpf_hash"), table_name="customers")
    op.drop_table("customers")

    # Remove os tipos enum criados implicitamente pelas tabelas acima.
    coupon_status.drop(op.get_bind(), checkfirst=True)
    coupon_discount_type.drop(op.get_bind(), checkfirst=True)
    ledger_entry_type.drop(op.get_bind(), checkfirst=True)
