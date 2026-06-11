"""PostgreSQL-ready SQLAlchemy 2 persistence records."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, foreign, mapped_column, relationship, validates

from llm_gateway.persistence.metadata import Base

JSON_OBJECT = MutableDict.as_mutable(JSON().with_variant(JSONB(), "postgresql"))
SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "completion",
    "content",
    "cookie",
    "credential",
    "message",
    "password",
    "prompt",
    "secret",
    "stop",
    "token",
    "user",
)
SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?i)(?:authorization|bearer\s+\S+|api[_-]?key|credential|password|"
    r"prompt|completion|secret|token|sk-(?:ant-)?[A-Za-z0-9_-]{8,})"
)


def _assert_privacy_safe[PrivacyValue](
    value: PrivacyValue,
    *,
    field_name: str,
) -> PrivacyValue:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = str(key).casefold().replace("-", "_")
            if any(part in normalized_key for part in SENSITIVE_KEY_PARTS):
                raise ValueError(f"{field_name} contains prohibited key {key!r}")
            _assert_privacy_safe(item, field_name=field_name)
    elif isinstance(value, list | tuple | set | frozenset):
        for item in value:
            _assert_privacy_safe(item, field_name=field_name)
    elif isinstance(value, str) and SENSITIVE_VALUE_PATTERN.search(value):
        raise ValueError(f"{field_name} contains sensitive data")
    return value


def _assert_safe_secret_ref(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) > 512 or "\r" in value or "\n" in value:
        raise ValueError("secret_ref must be a bounded single-line reference")
    if re.search(r"(?i)(?:bearer\s+\S+|sk-(?:ant-)?[A-Za-z0-9_-]{8,})", value):
        raise ValueError("secret_ref must not contain credential material")
    return value


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Provider(TimestampMixin, Base):
    __tablename__ = "providers"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    adapter: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    base_url: Mapped[str | None] = mapped_column(
        String(2048),
        comment="Operational provider endpoint; may disclose internal network topology.",
    )
    secret_ref: Mapped[str | None] = mapped_column(
        String(512),
        comment="Secret-manager lookup reference only; never stores credential material.",
    )
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSON_OBJECT,
        nullable=False,
        default=dict,
        comment="Allowlisted non-secret provider settings; credentials and content are prohibited.",
    )

    models: Mapped[list[Model]] = relationship(
        back_populates="provider",
        cascade="all, delete-orphan",
    )
    attempts: Mapped[list[ProviderAttempt]] = relationship(
        primaryjoin=lambda: Provider.id == foreign(ProviderAttempt.provider_id),
        viewonly=True,
    )

    @validates("secret_ref")
    def validate_secret_ref(self, _key: str, value: str | None) -> str | None:
        return _assert_safe_secret_ref(value)

    @validates("settings")
    def validate_settings(self, _key: str, value: dict[str, Any]) -> dict[str, Any]:
        return _assert_privacy_safe(value, field_name="settings")


class Model(TimestampMixin, Base):
    __tablename__ = "models"
    __table_args__ = (
        UniqueConstraint("provider_id", "upstream_name"),
        UniqueConstraint("id", "provider_id", name="uq_models_id_provider_id"),
        Index("ix_models_gateway_name_enabled", "gateway_name", "enabled"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    provider_id: Mapped[UUID] = mapped_column(
        ForeignKey("providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    gateway_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    upstream_name: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    capabilities: Mapped[dict[str, Any]] = mapped_column(
        JSON_OBJECT,
        nullable=False,
        default=dict,
        comment="Operational capability flags only; request and response content are prohibited.",
    )

    provider: Mapped[Provider] = relationship(back_populates="models")
    attempts: Mapped[list[ProviderAttempt]] = relationship(
        back_populates="model",
        foreign_keys="[ProviderAttempt.model_id, ProviderAttempt.provider_id]",
    )

    @validates("capabilities")
    def validate_capabilities(self, _key: str, value: dict[str, Any]) -> dict[str, Any]:
        return _assert_privacy_safe(value, field_name="capabilities")


class GatewayRequest(TimestampMixin, Base):
    __tablename__ = "gateway_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('received', 'in_progress', 'succeeded', 'failed', 'cancelled')",
            name="status_values",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    correlation_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="received",
        server_default="received",
        index=True,
    )
    requested_model: Mapped[str] = mapped_column(String(255), nullable=False)
    request_payload_redacted: Mapped[dict[str, Any] | None] = mapped_column(
        JSON_OBJECT,
        comment=(
            "Opt-in redacted content; null by default and governed by content retention policy."
        ),
    )
    error_type: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(
        Text,
        comment=(
            "Sanitized error summary only; raw provider bodies and prompt content are prohibited."
        ),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    attempts: Mapped[list[ProviderAttempt]] = relationship(
        back_populates="gateway_request",
        cascade="all, delete-orphan",
        order_by="ProviderAttempt.attempt_number",
    )
    usage_records: Mapped[list[UsageRecord]] = relationship(
        back_populates="gateway_request",
        cascade="all, delete-orphan",
    )
    audit_metadata_record: Mapped[AuditMetadata | None] = relationship(
        back_populates="gateway_request",
        cascade="all, delete-orphan",
        uselist=False,
    )

    @validates("request_payload_redacted")
    def validate_request_payload(
        self,
        _key: str,
        value: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        return _assert_privacy_safe(value, field_name="request_payload_redacted")

    @validates("error_message")
    def validate_error_message(self, _key: str, value: str | None) -> str | None:
        return _assert_privacy_safe(value, field_name="error_message")


class ProviderAttempt(TimestampMixin, Base):
    __tablename__ = "provider_attempts"
    __table_args__ = (
        UniqueConstraint("gateway_request_id", "attempt_number"),
        UniqueConstraint("id", "gateway_request_id", name="uq_provider_attempts_id_request_id"),
        ForeignKeyConstraint(
            ["model_id", "provider_id"],
            ["models.id", "models.provider_id"],
            name="fk_provider_attempts_model_provider",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('pending', 'in_progress', 'succeeded', 'failed', 'timed_out', 'cancelled')",
            name="status_values",
        ),
        CheckConstraint("attempt_number > 0", name="attempt_number_positive"),
        CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="latency_non_negative",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    gateway_request_id: Mapped[UUID] = mapped_column(
        ForeignKey("gateway_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider_id: Mapped[UUID] = mapped_column(
        nullable=False,
        index=True,
    )
    model_id: Mapped[UUID] = mapped_column(
        ForeignKey("models.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
    )
    upstream_request_id: Mapped[str | None] = mapped_column(
        String(255),
        comment="Confidential provider-issued operational identifier; not client-visible.",
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_type: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(
        Text,
        comment=(
            "Sanitized error summary only; raw provider bodies and prompt content are prohibited."
        ),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    gateway_request: Mapped[GatewayRequest] = relationship(back_populates="attempts")
    provider: Mapped[Provider] = relationship(
        primaryjoin=lambda: Provider.id == foreign(ProviderAttempt.provider_id),
        viewonly=True,
    )
    model: Mapped[Model] = relationship(
        back_populates="attempts",
        foreign_keys=[model_id, provider_id],
    )
    usage_records: Mapped[list[UsageRecord]] = relationship(
        back_populates="attempt",
        overlaps="gateway_request,usage_records",
    )

    @validates("error_message")
    def validate_error_message(self, _key: str, value: str | None) -> str | None:
        return _assert_privacy_safe(value, field_name="error_message")


class UsageRecord(Base):
    __tablename__ = "usage_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["provider_attempt_id", "gateway_request_id"],
            ["provider_attempts.id", "provider_attempts.gateway_request_id"],
            name="fk_usage_records_attempt_request",
            ondelete="RESTRICT",
        ),
        CheckConstraint("prompt_tokens >= 0", name="prompt_tokens_non_negative"),
        CheckConstraint(
            "completion_tokens >= 0",
            name="completion_tokens_non_negative",
        ),
        CheckConstraint("total_tokens >= 0", name="total_tokens_non_negative"),
        CheckConstraint(
            "total_tokens = prompt_tokens + completion_tokens",
            name="total_tokens_sum",
        ),
        CheckConstraint(
            "estimated_cost IS NULL OR estimated_cost >= 0",
            name="estimated_cost_non_negative",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    gateway_request_id: Mapped[UUID] = mapped_column(
        ForeignKey("gateway_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider_attempt_id: Mapped[UUID | None] = mapped_column(
        index=True,
    )
    prompt_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    currency: Mapped[str | None] = mapped_column(String(3))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    gateway_request: Mapped[GatewayRequest] = relationship(
        back_populates="usage_records",
        overlaps="attempt,usage_records",
    )
    attempt: Mapped[ProviderAttempt | None] = relationship(
        back_populates="usage_records",
        foreign_keys=[provider_attempt_id, gateway_request_id],
        overlaps="gateway_request,usage_records",
    )


class AuditMetadata(Base):
    __tablename__ = "audit_metadata"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    gateway_request_id: Mapped[UUID] = mapped_column(
        ForeignKey("gateway_requests.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    actor_hash: Mapped[str | None] = mapped_column(
        String(255),
        comment="Keyed pseudonymous identifier; personal data with restricted access.",
    )
    client_application: Mapped[str | None] = mapped_column(
        String(255),
        comment="Allowlisted client label; must not contain user-supplied content or credentials.",
    )
    source_ip_hash: Mapped[str | None] = mapped_column(
        String(255),
        comment="Keyed pseudonymous network identifier; personal data with restricted access.",
    )
    user_agent_hash: Mapped[str | None] = mapped_column(
        String(255),
        comment="Keyed pseudonymous client identifier; personal data with restricted access.",
    )
    retention_class: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="operational",
        server_default="operational",
    )
    tags: Mapped[dict[str, Any]] = mapped_column(
        JSON_OBJECT,
        nullable=False,
        default=dict,
        comment=(
            "Allowlisted audit labels only; content, secrets, and direct identifiers prohibited."
        ),
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    gateway_request: Mapped[GatewayRequest] = relationship(back_populates="audit_metadata_record")

    @validates("tags")
    def validate_tags(self, _key: str, value: dict[str, Any]) -> dict[str, Any]:
        return _assert_privacy_safe(value, field_name="tags")
