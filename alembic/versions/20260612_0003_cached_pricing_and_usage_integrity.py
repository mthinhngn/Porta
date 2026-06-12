"""Add cached-input pricing and usage completion integrity.

Revision ID: 20260612_0003
Revises: 20260611_0002
Create Date: 2026-06-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260612_0003"
down_revision: str | None = "20260611_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pricing_snapshots",
        sa.Column(
            "cached_input_cost_per_million",
            sa.Numeric(precision=20, scale=10),
            nullable=True,
        ),
    )
    op.execute(
        """
        UPDATE pricing_snapshots
        SET cached_input_cost_per_million = input_cost_per_million
        """
    )
    op.alter_column(
        "pricing_snapshots",
        "cached_input_cost_per_million",
        existing_type=sa.Numeric(precision=20, scale=10),
        nullable=False,
    )
    op.create_check_constraint(
        op.f("ck_pricing_snapshots_cached_input_cost_per_million_non_negative"),
        "pricing_snapshots",
        "cached_input_cost_per_million >= 0",
    )

    op.add_column(
        "usage_records",
        sa.Column(
            "cached_input_tokens",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_usage_records_cached_input_tokens_non_negative"),
        "usage_records",
        "cached_input_tokens >= 0",
    )
    op.create_check_constraint(
        op.f("ck_usage_records_cached_input_tokens_not_greater_than_prompt"),
        "usage_records",
        "cached_input_tokens <= prompt_tokens",
    )
    op.execute(
        """
        WITH ranked_usage AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY provider_attempt_id
                    ORDER BY recorded_at ASC, id ASC
                ) AS duplicate_rank
            FROM usage_records
            WHERE provider_attempt_id IS NOT NULL
        )
        DELETE FROM usage_records
        WHERE id IN (
            SELECT id
            FROM ranked_usage
            WHERE duplicate_rank > 1
        )
        """
    )
    op.create_unique_constraint(
        "uq_usage_records_provider_attempt_id",
        "usage_records",
        ["provider_attempt_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_usage_records_provider_attempt_id",
        "usage_records",
        type_="unique",
    )
    op.drop_constraint(
        op.f("ck_usage_records_cached_input_tokens_not_greater_than_prompt"),
        "usage_records",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_usage_records_cached_input_tokens_non_negative"),
        "usage_records",
        type_="check",
    )
    op.drop_column("usage_records", "cached_input_tokens")

    op.drop_constraint(
        op.f("ck_pricing_snapshots_cached_input_cost_per_million_non_negative"),
        "pricing_snapshots",
        type_="check",
    )
    op.drop_column("pricing_snapshots", "cached_input_cost_per_million")
