import asyncio
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


def test_exhausted_scripted_provider_fails_without_network() -> None:
    provider = ScriptedProvider("scripted", [])

    with pytest.raises(ProviderUnavailableError):
        asyncio.run(provider.complete(request(), context()))
