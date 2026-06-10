import io
import json
import logging

from llm_gateway.core.context import bind_correlation_id, reset_correlation_id
from llm_gateway.core.logging import (
    JsonFormatter,
    allowlisted_log_fields,
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
