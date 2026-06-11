"""Create the initial Phase 0 persistence schema.

Revision ID: 20260611_0001
Revises:
Create Date: 2026-06-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260611_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_OBJECT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "providers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("adapter", sa.String(length=128), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "base_url",
            sa.String(length=2048),
            nullable=True,
            comment="Operational provider endpoint; may disclose internal network topology.",
        ),
        sa.Column(
            "secret_ref",
            sa.String(length=512),
            nullable=True,
            comment="Secret-manager lookup reference only; never stores credential material.",
        ),
        sa.Column(
            "settings",
            JSON_OBJECT,
            nullable=False,
            comment=(
                "Allowlisted non-secret provider settings; credentials and content are prohibited."
            ),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_providers")),
        sa.UniqueConstraint("name", name=op.f("uq_providers_name")),
    )

    op.create_table(
        "gateway_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("correlation_id", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="received",
            nullable=False,
        ),
        sa.Column("requested_model", sa.String(length=255), nullable=False),
        sa.Column(
            "request_payload_redacted",
            JSON_OBJECT,
            nullable=True,
            comment=(
                "Opt-in redacted content; null by default and governed by content retention policy."
            ),
        ),
        sa.Column("error_type", sa.String(length=128), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column(
            "error_message",
            sa.Text(),
            nullable=True,
            comment=(
                "Sanitized error summary only; raw provider bodies and "
                "prompt content are prohibited."
            ),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('received', 'in_progress', 'succeeded', 'failed', 'cancelled')",
            name=op.f("ck_gateway_requests_status_values"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_gateway_requests")),
    )
    op.create_index(
        op.f("ix_gateway_requests_correlation_id"),
        "gateway_requests",
        ["correlation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_gateway_requests_status"),
        "gateway_requests",
        ["status"],
        unique=False,
    )

    op.create_table(
        "models",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("gateway_name", sa.String(length=255), nullable=False),
        sa.Column("upstream_name", sa.String(length=255), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "capabilities",
            JSON_OBJECT,
            nullable=False,
            comment=(
                "Operational capability flags only; request and response content are prohibited."
            ),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["providers.id"],
            name=op.f("fk_models_provider_id_providers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_models")),
        sa.UniqueConstraint("id", "provider_id", name="uq_models_id_provider_id"),
        sa.UniqueConstraint(
            "provider_id",
            "upstream_name",
            name=op.f("uq_models_provider_id"),
        ),
    )
    op.create_index(
        "ix_models_gateway_name_enabled",
        "models",
        ["gateway_name", "enabled"],
        unique=False,
    )
    op.create_index(
        op.f("ix_models_gateway_name"),
        "models",
        ["gateway_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_models_provider_id"),
        "models",
        ["provider_id"],
        unique=False,
    )

    op.create_table(
        "audit_metadata",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("gateway_request_id", sa.Uuid(), nullable=False),
        sa.Column(
            "actor_hash",
            sa.String(length=255),
            nullable=True,
            comment="Keyed pseudonymous identifier; personal data with restricted access.",
        ),
        sa.Column(
            "client_application",
            sa.String(length=255),
            nullable=True,
            comment=(
                "Allowlisted client label; must not contain user-supplied content or credentials."
            ),
        ),
        sa.Column(
            "source_ip_hash",
            sa.String(length=255),
            nullable=True,
            comment=(
                "Keyed pseudonymous network identifier; personal data with restricted access."
            ),
        ),
        sa.Column(
            "user_agent_hash",
            sa.String(length=255),
            nullable=True,
            comment=("Keyed pseudonymous client identifier; personal data with restricted access."),
        ),
        sa.Column(
            "retention_class",
            sa.String(length=64),
            server_default="operational",
            nullable=False,
        ),
        sa.Column(
            "tags",
            JSON_OBJECT,
            nullable=False,
            comment=(
                "Allowlisted audit labels only; content, secrets, and "
                "direct identifiers prohibited."
            ),
        ),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["gateway_request_id"],
            ["gateway_requests.id"],
            name=op.f("fk_audit_metadata_gateway_request_id_gateway_requests"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_metadata")),
        sa.UniqueConstraint(
            "gateway_request_id",
            name=op.f("uq_audit_metadata_gateway_request_id"),
        ),
    )

    op.create_table(
        "provider_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("gateway_request_id", sa.Uuid(), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("model_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "upstream_request_id",
            sa.String(length=255),
            nullable=True,
            comment="Confidential provider-issued operational identifier; not client-visible.",
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_type", sa.String(length=128), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column(
            "error_message",
            sa.Text(),
            nullable=True,
            comment=(
                "Sanitized error summary only; raw provider bodies and "
                "prompt content are prohibited."
            ),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'succeeded', 'failed', 'timed_out', 'cancelled')",
            name=op.f("ck_provider_attempts_status_values"),
        ),
        sa.CheckConstraint(
            "attempt_number > 0",
            name=op.f("ck_provider_attempts_attempt_number_positive"),
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name=op.f("ck_provider_attempts_latency_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["gateway_request_id"],
            ["gateway_requests.id"],
            name=op.f("fk_provider_attempts_gateway_request_id_gateway_requests"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["models.id"],
            name=op.f("fk_provider_attempts_model_id_models"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["model_id", "provider_id"],
            ["models.id", "models.provider_id"],
            name="fk_provider_attempts_model_provider",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_attempts")),
        sa.UniqueConstraint(
            "gateway_request_id",
            "attempt_number",
            name=op.f("uq_provider_attempts_gateway_request_id"),
        ),
        sa.UniqueConstraint(
            "id",
            "gateway_request_id",
            name="uq_provider_attempts_id_request_id",
        ),
    )
    op.create_index(
        op.f("ix_provider_attempts_gateway_request_id"),
        "provider_attempts",
        ["gateway_request_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_provider_attempts_model_id"),
        "provider_attempts",
        ["model_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_provider_attempts_provider_id"),
        "provider_attempts",
        ["provider_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_provider_attempts_status"),
        "provider_attempts",
        ["status"],
        unique=False,
    )

    op.create_table(
        "usage_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("gateway_request_id", sa.Uuid(), nullable=False),
        sa.Column("provider_attempt_id", sa.Uuid(), nullable=True),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=False),
        sa.Column("completion_tokens", sa.BigInteger(), nullable=False),
        sa.Column("total_tokens", sa.BigInteger(), nullable=False),
        sa.Column("estimated_cost", sa.Numeric(precision=20, scale=10), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "prompt_tokens >= 0",
            name=op.f("ck_usage_records_prompt_tokens_non_negative"),
        ),
        sa.CheckConstraint(
            "completion_tokens >= 0",
            name=op.f("ck_usage_records_completion_tokens_non_negative"),
        ),
        sa.CheckConstraint(
            "total_tokens >= 0",
            name=op.f("ck_usage_records_total_tokens_non_negative"),
        ),
        sa.CheckConstraint(
            "total_tokens = prompt_tokens + completion_tokens",
            name=op.f("ck_usage_records_total_tokens_sum"),
        ),
        sa.CheckConstraint(
            "estimated_cost IS NULL OR estimated_cost >= 0",
            name=op.f("ck_usage_records_estimated_cost_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["gateway_request_id"],
            ["gateway_requests.id"],
            name=op.f("fk_usage_records_gateway_request_id_gateway_requests"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["provider_attempt_id", "gateway_request_id"],
            ["provider_attempts.id", "provider_attempts.gateway_request_id"],
            name="fk_usage_records_attempt_request",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_records")),
    )
    op.create_index(
        op.f("ix_usage_records_gateway_request_id"),
        "usage_records",
        ["gateway_request_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_usage_records_provider_attempt_id"),
        "usage_records",
        ["provider_attempt_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_usage_records_provider_attempt_id"),
        table_name="usage_records",
    )
    op.drop_index(
        op.f("ix_usage_records_gateway_request_id"),
        table_name="usage_records",
    )
    op.drop_table("usage_records")
    op.drop_index(op.f("ix_provider_attempts_status"), table_name="provider_attempts")
    op.drop_index(op.f("ix_provider_attempts_provider_id"), table_name="provider_attempts")
    op.drop_index(op.f("ix_provider_attempts_model_id"), table_name="provider_attempts")
    op.drop_index(
        op.f("ix_provider_attempts_gateway_request_id"),
        table_name="provider_attempts",
    )
    op.drop_table("provider_attempts")
    op.drop_table("audit_metadata")
    op.drop_index(op.f("ix_models_provider_id"), table_name="models")
    op.drop_index(op.f("ix_models_gateway_name"), table_name="models")
    op.drop_index("ix_models_gateway_name_enabled", table_name="models")
    op.drop_table("models")
    op.drop_index(op.f("ix_gateway_requests_status"), table_name="gateway_requests")
    op.drop_index(
        op.f("ix_gateway_requests_correlation_id"),
        table_name="gateway_requests",
    )
    op.drop_table("gateway_requests")
    op.drop_table("providers")
