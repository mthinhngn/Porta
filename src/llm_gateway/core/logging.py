"""Allowlisted JSON logging that excludes confidential request content."""

import json
import logging
import math
import re
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
    "body",
    "completion",
    "cookie",
    "credential",
    "header",
    "message_content",
    "password",
    "prompt",
    "provider_secret",
    "query",
    "request_content",
    "response_content",
    "secret",
    "stop",
    "token",
)
REDACTED = "[REDACTED]"
MAX_LOG_STRING_LENGTH = 512
SUSPICIOUS_VALUE_PATTERNS = (
    re.compile(r"(?i)\b(?:bearer|basic)\s+\S+"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|authorization|completion|cookie|credential|"
        r"message[_-]?content|password|prompt|provider[_-]?secret|secret|"
        r"stop|token)\b"
    ),
    re.compile(r"\beyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"(?i)\b(?:sk|rk|pk)-(?:ant-)?[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)://[^/\s:@]+:[^/\s@]+@"),
)
_HANDLER_MARKER = "_llm_gateway_json_handler"
_FILTER_MARKER = "_llm_gateway_redaction_filter"
_STANDARD_LOG_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)


def is_sensitive_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def sanitize_log_message(message: Any) -> str:
    """Render a bounded message, redacting values that resemble confidential data."""

    text = str(message)
    if len(text) > MAX_LOG_STRING_LENGTH:
        return REDACTED
    if any(pattern.search(text) for pattern in SUSPICIOUS_VALUE_PATTERNS):
        return REDACTED
    return text


def sanitize_log_value(value: Any) -> str | int | float | bool | None:
    """Return a JSON-safe operational scalar or a redaction marker."""

    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else REDACTED
    if isinstance(value, str):
        return sanitize_log_message(value)
    return REDACTED


def allowlisted_log_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Return sanitized operational fields, omitting sensitive keys entirely."""

    return {
        key: sanitize_log_value(value)
        for key, value in fields.items()
        if key in ALLOWED_LOG_FIELDS
        and not is_sensitive_key(key)
        and (
            value is None
            or isinstance(value, (str, bool, int))
            or (isinstance(value, float) and math.isfinite(value))
        )
    }


class SensitiveDataFilter(logging.Filter):
    """Scrub a record before any configured handler can render it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = sanitize_log_message(record.getMessage())
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None

        structured_fields = getattr(record, "structured_fields", None)
        if isinstance(structured_fields, Mapping):
            record.structured_fields = allowlisted_log_fields(structured_fields)

        for key in tuple(record.__dict__):
            if key in _STANDARD_LOG_RECORD_FIELDS or key == "structured_fields":
                continue
            if is_sensitive_key(key):
                del record.__dict__[key]
                continue
            record.__dict__[key] = sanitize_log_value(record.__dict__[key])
        return True


class JsonFormatter(logging.Formatter):
    """Render one compact JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        fields: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": sanitize_log_message(record.getMessage()),
        }
        correlation_id = correlation_id_context.get()
        if correlation_id is not None:
            fields["correlation_id"] = sanitize_log_value(correlation_id)

        structured_fields = getattr(record, "structured_fields", {})
        if isinstance(structured_fields, Mapping):
            fields.update(allowlisted_log_fields(structured_fields))
        return json.dumps(allowlisted_log_fields(fields), separators=(",", ":"))


def configure_logging(level: str) -> None:
    """Install one gateway JSON handler while preserving external root handlers."""

    root_logger = logging.getLogger()
    gateway_handlers = [
        handler for handler in root_logger.handlers if getattr(handler, _HANDLER_MARKER, False)
    ]
    if gateway_handlers:
        handler = gateway_handlers[0]
        for duplicate in gateway_handlers[1:]:
            root_logger.removeHandler(duplicate)
    else:
        handler = logging.StreamHandler()
        setattr(handler, _HANDLER_MARKER, True)
        root_logger.addHandler(handler)

    handler.setFormatter(JsonFormatter())
    handler.setLevel(logging.NOTSET)
    for root_handler in root_logger.handlers:
        if not any(getattr(item, _FILTER_MARKER, False) for item in root_handler.filters):
            redaction_filter = SensitiveDataFilter()
            setattr(redaction_filter, _FILTER_MARKER, True)
            root_handler.addFilter(redaction_filter)
    root_logger.setLevel(level)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    **fields: Any,
) -> None:
    logger.log(
        level,
        sanitize_log_message(event),
        extra={"structured_fields": allowlisted_log_fields(fields)},
    )
