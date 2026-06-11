import asyncio
import math
from dataclasses import FrozenInstanceError
from typing import Any, cast
from uuid import uuid4

import pytest

from llm_gateway.domain import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    ChatRole,
    TokenUsage,
)
from llm_gateway.providers import (
    ChatCompletionProvider,
    ProviderContext,
    ProviderRateLimitError,
    ProviderUnavailableError,
    ScriptedProvider,
)


def request() -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="gateway-model",
        messages=[ChatMessage(role=ChatRole.USER, content="private prompt")],
    )


def response() -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id="completion-1",
        created=1,
        model="gateway-model",
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ChatMessage(role=ChatRole.ASSISTANT, content="private output"),
                finish_reason="stop",
            )
        ],
        usage=TokenUsage(prompt_tokens=2, completion_tokens=2, total_tokens=4),
    )


def context() -> ProviderContext:
    return ProviderContext(
        gateway_request_id=uuid4(),
        correlation_id="correlation-1",
        provider_name="scripted",
        model_name="upstream-model",
        timeout_seconds=10,
    )


def test_scripted_provider_returns_response_and_records_call() -> None:
    provider = ScriptedProvider("scripted", [response()])

    result = asyncio.run(provider.complete(request(), context()))

    assert isinstance(provider, ChatCompletionProvider)
    assert result.id == "completion-1"
    assert provider.remaining_steps == 0
    assert len(provider.calls) == 1


def test_scripted_provider_preserves_typed_error() -> None:
    provider = ScriptedProvider(
        "scripted",
        [ProviderRateLimitError("sanitized", provider_request_id="request-1")],
    )

    with pytest.raises(ProviderRateLimitError) as exc_info:
        asyncio.run(provider.complete(request(), context()))

    assert exc_info.value.retryable is True
    assert exc_info.value.code == "provider_rate_limit"


def test_provider_error_does_not_retain_sensitive_message_or_details() -> None:
    error = ProviderRateLimitError(
        "authorization=Bearer private-secret",
        details={
            "prompt": "private prompt",
            "status_code": 429,
            "error_code": "rate_limited",
        },
    )

    assert error.message == "Provider request failed."
    assert dict(error.details) == {"status_code": 429, "error_code": "rate_limited"}
    assert "private" not in str(error)


def test_exhausted_scripted_provider_fails_without_network() -> None:
    provider = ScriptedProvider("scripted", [])

    with pytest.raises(ProviderUnavailableError):
        asyncio.run(provider.complete(request(), context()))


def test_provider_context_metadata_is_an_immutable_snapshot() -> None:
    source = {
        "routing": {"region": "us-west", "fallbacks": ["secondary"]},
        "labels": {"private", "test"},
    }
    provider_context = ProviderContext(
        gateway_request_id=uuid4(),
        correlation_id="correlation-1",
        provider_name="scripted",
        model_name="upstream-model",
        timeout_seconds=10,
        metadata=source,
    )

    cast(dict[str, Any], source["routing"])["region"] = "changed"
    cast(list[str], cast(dict[str, Any], source["routing"])["fallbacks"]).append("third")

    routing = cast(dict[str, Any], provider_context.metadata["routing"])
    assert routing["region"] == "us-west"
    assert routing["fallbacks"] == ("secondary",)
    assert provider_context.metadata["labels"] == frozenset({"private", "test"})

    with pytest.raises(TypeError):
        routing["region"] = "changed"

    with pytest.raises(FrozenInstanceError):
        provider_context.provider_name = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("timeout_seconds", [0, -1, math.inf, -math.inf, math.nan])
def test_provider_context_requires_positive_finite_timeout(timeout_seconds: float) -> None:
    with pytest.raises(ValueError, match="positive and finite"):
        ProviderContext(
            gateway_request_id=uuid4(),
            correlation_id="correlation-1",
            provider_name="scripted",
            model_name="upstream-model",
            timeout_seconds=timeout_seconds,
        )
