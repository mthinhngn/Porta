import io
import json
import logging

from llm_gateway.core.context import bind_correlation_id, reset_correlation_id
from llm_gateway.core.logging import (
    JsonFormatter,
    allowlisted_log_fields,
    configure_logging,
    log_event,
)


def test_allowlist_omits_content_and_secret_values() -> None:
    safe = allowlisted_log_fields(
        {
            "event": "provider_attempt_failed",
            "provider_name": "test",
            "prompt": "private prompt",
            "completion": "private completion",
            "authorization": "Bearer private-auth",
            "cookie": "private-cookie",
            "api_key": "private-key",
            "client_secret": "private-secret",
            "unknown": "also omitted",
        }
    )

    serialized = json.dumps(safe)
    assert safe == {"event": "provider_attempt_failed", "provider_name": "test"}
    for private_value in (
        "private prompt",
        "private completion",
        "private-auth",
        "private-cookie",
        "private-key",
        "private-secret",
        "also omitted",
    ):
        assert private_value not in serialized


def test_json_logging_includes_only_operational_context() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test.json")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    token = bind_correlation_id("correlation-1")

    try:
        log_event(
            logger,
            logging.INFO,
            "request_completed",
            method="GET",
            path="/health/live",
            status_code=200,
            prompt="private prompt",
            api_key="private-key",
        )
    finally:
        reset_correlation_id(token)

    payload = json.loads(stream.getvalue())
    assert payload["event"] == "request_completed"
    assert payload["correlation_id"] == "correlation-1"
    assert payload["method"] == "GET"
    assert payload["status_code"] == 200
    assert "private prompt" not in stream.getvalue()
    assert "private-key" not in stream.getvalue()


def test_allowed_fields_redact_suspicious_values_and_non_scalars() -> None:
    safe = allowlisted_log_fields(
        {
            "event": "Bearer private-token",
            "provider_name": "sk-provider-secret-value",
            "method": ["GET"],
            "status_code": 200,
        }
    )

    assert safe == {
        "event": "[REDACTED]",
        "provider_name": "[REDACTED]",
        "status_code": 200,
    }


def test_formatter_sanitizes_an_ordinary_log_message() -> None:
    record = logging.LogRecord(
        name="test.ordinary",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="provider failed with authorization=Bearer private-token",
        args=(),
        exc_info=None,
    )

    payload = json.loads(JsonFormatter().format(record))

    assert payload["event"] == "[REDACTED]"
    assert "private-token" not in json.dumps(payload)


def test_configure_logging_is_idempotent_and_preserves_external_handlers() -> None:
    root_logger = logging.getLogger()
    original_handlers = root_logger.handlers[:]
    original_level = root_logger.level
    external_handler = logging.NullHandler()

    try:
        root_logger.handlers = [external_handler]

        configure_logging("INFO")
        configure_logging("DEBUG")

        gateway_handlers = [
            handler
            for handler in root_logger.handlers
            if isinstance(handler.formatter, JsonFormatter)
        ]
        assert external_handler in root_logger.handlers
        assert len(gateway_handlers) == 1
        assert root_logger.level == logging.DEBUG
    finally:
        root_logger.handlers = original_handlers
        root_logger.setLevel(original_level)


def test_external_root_handler_receives_sanitized_record() -> None:
    stream = io.StringIO()
    external_handler = logging.StreamHandler(stream)
    external_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger = logging.getLogger()
    original_handlers = root_logger.handlers[:]
    original_level = root_logger.level

    try:
        root_logger.handlers = [external_handler]
        configure_logging("INFO")

        logging.getLogger("test.external").error(
            "authorization=Bearer private-secret",
            extra={"prompt": "private prompt"},
        )

        assert "private-secret" not in stream.getvalue()
        assert "private prompt" not in stream.getvalue()
        assert "[REDACTED]" in stream.getvalue()
    finally:
        root_logger.handlers = original_handlers
        root_logger.setLevel(original_level)
