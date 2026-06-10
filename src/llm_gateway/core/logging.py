"""Allowlisted JSON logging that excludes confidential request content."""

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from llm_gateway.core.context import correlation_id_context

ALLOWED_LOG_FIELDS = frozenset(
    {
        "correlation_id",
        "duration_ms",
        "environment",
        "error_code",
        "event",
        "gateway_request_id",
        "level",
        "logger",
        "method",
        "path",
        "provider_attempt_id",
        "provider_name",
        "status_code",
        "timestamp",
    }
)
SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "completion",
    "cookie",
    "credential",
    "message_content",
    "prompt",
    "secret",
    "stop",
    "token",
)


def is_sensitive_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def allowlisted_log_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Return only known operational fields, omitting sensitive keys entirely."""

    return {
        key: value
        for key, value in fields.items()
        if key in ALLOWED_LOG_FIELDS and not is_sensitive_key(key)
    }


class JsonFormatter(logging.Formatter):
    """Render one compact JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        fields: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        correlation_id = correlation_id_context.get()
        if correlation_id is not None:
            fields["correlation_id"] = correlation_id

        structured_fields = getattr(record, "structured_fields", {})
        if isinstance(structured_fields, Mapping):
            fields.update(allowlisted_log_fields(structured_fields))
        return json.dumps(allowlisted_log_fields(fields), separators=(",", ":"))


def configure_logging(level: str) -> None:
    """Configure the root logger without exposing process configuration."""

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    **fields: Any,
) -> None:
    logger.log(
        level,
        event,
        extra={"structured_fields": allowlisted_log_fields(fields)},
    )
