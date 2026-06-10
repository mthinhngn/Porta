"""PostgreSQL-ready SQLAlchemy 2 persistence records."""

from __future__ import annotations

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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from llm_gateway.persistence.metadata import Base

JSON_OBJECT = MutableDict.as_mutable(JSON().with_variant(JSONB(), "postgresql"))


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
    base_url: Mapped[str | None] = mapped_column(String(2048))
    secret_ref: Mapped[str | None] = mapped_column(String(512))
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSON_OBJECT,
        nullable=False,
        default=dict,
    )

    models: Mapped[list[Model]] = relationship(
        back_populates="provider",
        cascade="all, delete-orphan",
    )
    attempts: Mapped[list[ProviderAttempt]] = relationship(back_populates="provider")


class Model(TimestampMixin, Base):
    __tablename__ = "models"
    __table_args__ = (
        UniqueConstraint("provider_id", "upstream_name"),
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
    )

    provider: Mapped[Provider] = relationship(back_populates="models")
    attempts: Mapped[list[ProviderAttempt]] = relationship(back_populates="model")


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
    request_payload_redacted: Mapped[dict[str, Any] | None] = mapped_column(JSON_OBJECT)
    error_type: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
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


class ProviderAttempt(TimestampMixin, Base):
    __tablename__ = "provider_attempts"
    __table_args__ = (
        UniqueConstraint("gateway_request_id", "attempt_number"),
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
        ForeignKey("providers.id", ondelete="RESTRICT"),
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
    upstream_request_id: Mapped[str | None] = mapped_column(String(255))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_type: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    gateway_request: Mapped[GatewayRequest] = relationship(back_populates="attempts")
    provider: Mapped[Provider] = relationship(back_populates="attempts")
    model: Mapped[Model] = relationship(back_populates="attempts")
    usage_records: Mapped[list[UsageRecord]] = relationship(back_populates="attempt")


class UsageRecord(Base):
    __tablename__ = "usage_records"
    __table_args__ = (
        CheckConstraint("prompt_tokens >= 0", name="prompt_tokens_non_negative"),
        CheckConstraint(
            "completion_tokens >= 0",
            name="completion_tokens_non_negative",
        ),
        CheckConstraint("total_tokens >= 0", name="total_tokens_non_negative"),
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
        ForeignKey("provider_attempts.id", ondelete="SET NULL"),
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

    gateway_request: Mapped[GatewayRequest] = relationship(back_populates="usage_records")
    attempt: Mapped[ProviderAttempt | None] = relationship(back_populates="usage_records")


class AuditMetadata(Base):
    __tablename__ = "audit_metadata"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    gateway_request_id: Mapped[UUID] = mapped_column(
        ForeignKey("gateway_requests.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    actor_hash: Mapped[str | None] = mapped_column(String(255))
    client_application: Mapped[str | None] = mapped_column(String(255))
    source_ip_hash: Mapped[str | None] = mapped_column(String(255))
    user_agent_hash: Mapped[str | None] = mapped_column(String(255))
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
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    gateway_request: Mapped[GatewayRequest] = relationship(back_populates="audit_metadata_record")
