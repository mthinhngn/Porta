import io
import json
import logging

import pytest

from llm_gateway.core.logging import JsonFormatter, log_event


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider_name", "Bearer provider-token"),
        ("error_code", "completion=private-completion"),
        ("gateway_request_id", "cookie=session-secret"),
        ("provider_attempt_id", "sk-ant-provider-secret-value"),
        ("path", "/route?api_key=private-key"),
    ],
)
def test_suspicious_values_cannot_escape_through_allowed_fields(
    field: str,
    value: str,
) -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger(f"test.privacy.{field}")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    log_event(logger, logging.INFO, "safe_event", **{field: value})

    payload = json.loads(stream.getvalue())
    assert value not in stream.getvalue()
    assert payload[field] == "[REDACTED]"


def test_sensitive_keys_are_omitted_instead_of_redacted() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test.privacy.keys")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    log_event(
        logger,
        logging.INFO,
        "safe_event",
        prompt="private prompt",
        completion="private completion",
        cookie="private cookie",
        provider_secret="private provider secret",
    )

    payload = json.loads(stream.getvalue())
    assert payload["event"] == "safe_event"
    assert "prompt" not in payload
    assert "completion" not in payload
    assert "cookie" not in payload
    assert "provider_secret" not in payload
    assert "private" not in stream.getvalue()
