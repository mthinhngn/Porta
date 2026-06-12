import asyncio
import math
from dataclasses import FrozenInstanceError
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest

from llm_gateway.domain import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    ChatRole,
    GenerateRequest,
    GenerateTokenUsage,
    TokenUsage,
)
from llm_gateway.providers import (
    ChatCompletionProvider,
    GenerateProvider,
    GenerateProviderContext,
    OpenAIResponsesProvider,
    ProviderAuthenticationError,
    ProviderBadRequestError,
    ProviderContext,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
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


def generate_request() -> GenerateRequest:
    return GenerateRequest(model="gateway-default", input="private prompt", max_output_tokens=32)


def generate_context() -> GenerateProviderContext:
    return GenerateProviderContext(
        gateway_request_id=uuid4(),
        correlation_id="correlation-1",
        provider_name="openai",
        model_name="gpt-4.1-mini",
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


def test_provider_error_replaces_unsafe_codes_with_defaults() -> None:
    error = ProviderUnavailableError(
        "offline",
        code="DROP TABLE providers; --",
    )

    assert error.code == "provider_unavailable"


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


def test_generate_provider_context_requires_positive_finite_timeout() -> None:
    with pytest.raises(ValueError, match="positive and finite"):
        GenerateProviderContext(
            gateway_request_id=uuid4(),
            correlation_id="correlation-1",
            provider_name="openai",
            model_name="gpt-4.1-mini",
            timeout_seconds=0,
        )


def test_openai_responses_adapter_normalizes_success_and_sets_store_false() -> None:
    seen_payload: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_payload
        seen_payload = cast(dict[str, Any], json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            status_code=200,
            json={
                "id": "resp_123",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "hello world"}],
                    }
                ],
                "usage": {
                    "input_tokens": 2,
                    "output_tokens": 3,
                    "total_tokens": 5,
                    "input_tokens_details": {"cached_tokens": 1},
                },
            },
        )

    import json

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAIResponsesProvider(
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        client=client,
    )

    result = asyncio.run(provider.generate(generate_request(), generate_context()))

    assert isinstance(provider, GenerateProvider)
    assert result.output == "hello world"
    assert result.provider_request_id == "resp_123"
    assert result.usage == GenerateTokenUsage(input_tokens=2, output_tokens=3, total_tokens=5)
    assert result.cache_status == "hit"
    assert seen_payload["store"] is False
    assert seen_payload["model"] == "gpt-4.1-mini"

    asyncio.run(client.aclose())


def test_openai_responses_adapter_maps_timeout() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAIResponsesProvider(
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        client=client,
    )

    with pytest.raises(ProviderTimeoutError):
        asyncio.run(provider.generate(generate_request(), generate_context()))

    asyncio.run(client.aclose())


@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    [
        (400, ProviderBadRequestError),
        (404, ProviderBadRequestError),
        (409, ProviderBadRequestError),
        (422, ProviderBadRequestError),
        (401, ProviderAuthenticationError),
        (403, ProviderAuthenticationError),
        (429, ProviderRateLimitError),
        (500, ProviderUnavailableError),
    ],
)
def test_openai_responses_adapter_maps_http_statuses(
    status_code: int,
    expected_error: type[Exception],
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=status_code, json={"error": {"message": "ignored"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAIResponsesProvider(
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        client=client,
    )

    with pytest.raises(expected_error):
        asyncio.run(provider.generate(generate_request(), generate_context()))

    asyncio.run(client.aclose())


def test_openai_responses_adapter_rejects_malformed_payload() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, json={"id": "resp_123", "usage": {}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAIResponsesProvider(
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        client=client,
    )

    with pytest.raises(ProviderResponseError):
        asyncio.run(provider.generate(generate_request(), generate_context()))

    asyncio.run(client.aclose())


@pytest.mark.parametrize(
    "usage",
    [
        {"input_tokens": 1.5, "output_tokens": 2, "total_tokens": 3},
        {"input_tokens": True, "output_tokens": 2, "total_tokens": 3},
        {"input_tokens": -1, "output_tokens": 2, "total_tokens": 1},
        {"output_tokens": 2, "total_tokens": 2},
        {"input_tokens": 1, "output_tokens": 2, "total_tokens": 99},
    ],
)
def test_openai_responses_adapter_rejects_malformed_usage(usage: dict[str, object]) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "id": "resp_123",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "hello"}],
                    }
                ],
                "usage": usage,
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAIResponsesProvider(
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        client=client,
    )

    with pytest.raises(ProviderResponseError):
        asyncio.run(provider.generate(generate_request(), generate_context()))

    asyncio.run(client.aclose())
