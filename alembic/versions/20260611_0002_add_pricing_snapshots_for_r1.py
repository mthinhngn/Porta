"""Add pricing snapshots for R1 generate accounting.

Revision ID: 20260611_0002
Revises: 20260611_0001
Create Date: 2026-06-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260611_0002"
down_revision: str | None = "20260611_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pricing_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("model_id", sa.Uuid(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("input_cost_per_million", sa.Numeric(precision=20, scale=10), nullable=False),
        sa.Column("output_cost_per_million", sa.Numeric(precision=20, scale=10), nullable=False),
        sa.Column(
            "effective_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "input_cost_per_million >= 0",
            name=op.f("ck_pricing_snapshots_input_cost_per_million_non_negative"),
        ),
        sa.CheckConstraint(
            "output_cost_per_million >= 0",
            name=op.f("ck_pricing_snapshots_output_cost_per_million_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["model_id", "provider_id"],
            ["models.id", "models.provider_id"],
            name="fk_pricing_snapshots_model_provider",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["models.id"],
            name=op.f("fk_pricing_snapshots_model_id_models"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["providers.id"],
            name=op.f("fk_pricing_snapshots_provider_id_providers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pricing_snapshots")),
        sa.UniqueConstraint(
            "provider_id",
            "model_id",
            "effective_at",
            name="uq_pricing_snapshots_provider_model_effective_at",
        ),
    )
    op.create_index(
        op.f("ix_pricing_snapshots_model_id"),
        "pricing_snapshots",
        ["model_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_pricing_snapshots_provider_id"),
        "pricing_snapshots",
        ["provider_id"],
        unique=False,
    )
    op.add_column(
        "usage_records",
        sa.Column("pricing_snapshot_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        op.f("ix_usage_records_pricing_snapshot_id"),
        "usage_records",
        ["pricing_snapshot_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_usage_records_pricing_snapshot_id_pricing_snapshots"),
        "usage_records",
        "pricing_snapshots",
        ["pricing_snapshot_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_usage_records_pricing_snapshot_id_pricing_snapshots"),
        "usage_records",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_usage_records_pricing_snapshot_id"), table_name="usage_records")
    op.drop_column("usage_records", "pricing_snapshot_id")
    op.drop_index(op.f("ix_pricing_snapshots_provider_id"), table_name="pricing_snapshots")
    op.drop_index(op.f("ix_pricing_snapshots_model_id"), table_name="pricing_snapshots")
    op.drop_table("pricing_snapshots")
