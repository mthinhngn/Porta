import pytest
from pydantic import ValidationError

from llm_gateway.domain import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    ChatRole,
    ErrorDetail,
    ErrorResponse,
    GenerateCost,
    GenerateRequest,
    GenerateResponse,
    GenerateTokenUsage,
    TokenUsage,
)
from llm_gateway.domain.chat import FinishReason


def test_chat_request_schema_accepts_supported_shape() -> None:
    request = ChatCompletionRequest(
        model="gateway-model",
        messages=[ChatMessage(role=ChatRole.USER, content="hello")],
        temperature=0.2,
        stop=["END"],
    )

    assert request.model_dump() == {
        "model": "gateway-model",
        "messages": [
            {
                "role": ChatRole.USER,
                "content": "hello",
                "name": None,
                "tool_call_id": None,
            }
        ],
        "stream": False,
        "temperature": 0.2,
        "top_p": None,
        "n": None,
        "max_tokens": None,
        "max_completion_tokens": None,
        "presence_penalty": None,
        "frequency_penalty": None,
        "stop": ["END"],
        "seed": None,
        "user": None,
    }


def test_chat_request_schema_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ChatCompletionRequest.model_validate(
            {
                "model": "gateway-model",
                "messages": [{"role": "user", "content": "hello"}],
                "unsupported": True,
            }
        )


def test_generate_request_accepts_r1_shape() -> None:
    request = GenerateRequest(
        model="gateway-default",
        input="hello",
        temperature=0.2,
        max_output_tokens=64,
    )

    assert request.model_dump() == {
        "model": "gateway-default",
        "input": "hello",
        "temperature": 0.2,
        "top_p": None,
        "max_output_tokens": 64,
        "user": None,
    }


def test_generate_token_usage_rejects_inconsistent_total() -> None:
    with pytest.raises(ValidationError):
        GenerateTokenUsage(input_tokens=2, output_tokens=3, total_tokens=4)


def test_chat_request_accepts_explicit_non_streaming_mode() -> None:
    request = ChatCompletionRequest.model_validate(
        {
            "model": "gateway-model",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        }
    )

    assert request.stream is False


def test_chat_request_rejects_streaming_mode() -> None:
    with pytest.raises(ValidationError):
        ChatCompletionRequest.model_validate(
            {
                "model": "gateway-model",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
            }
        )


def test_token_usage_rejects_inconsistent_total() -> None:
    with pytest.raises(ValidationError):
        TokenUsage(prompt_tokens=2, completion_tokens=3, total_tokens=4)


@pytest.mark.parametrize(
    ("role", "tool_call_id"),
    [
        (ChatRole.TOOL, None),
        (ChatRole.USER, "call-1"),
        (ChatRole.ASSISTANT, "call-1"),
    ],
)
def test_chat_message_enforces_tool_call_id_role(
    role: ChatRole,
    tool_call_id: str | None,
) -> None:
    with pytest.raises(ValidationError):
        ChatMessage(role=role, content="hello", tool_call_id=tool_call_id)


def test_tool_message_accepts_tool_call_id() -> None:
    message = ChatMessage(role=ChatRole.TOOL, content="result", tool_call_id="call-1")

    assert message.tool_call_id == "call-1"


def test_chat_response_and_error_schemas() -> None:
    response = ChatCompletionResponse(
        id="completion-1",
        created=1,
        model="gateway-model",
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ChatMessage(role=ChatRole.ASSISTANT, content="hello"),
                finish_reason="stop",
            )
        ],
        usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )
    error = ErrorResponse(
        error=ErrorDetail(
            message="Invalid request.",
            type="invalid_request_error",
            param="model",
            code="validation_error",
        )
    )

    assert response.object == "chat.completion"
    assert response.choices[0].finish_reason is FinishReason.STOP
    assert response.usage.total_tokens == 2
    assert error.model_dump() == {
        "error": {
            "message": "Invalid request.",
            "type": "invalid_request_error",
            "param": "model",
            "code": "validation_error",
        }
    }


def test_generate_response_schema() -> None:
    response = GenerateResponse(
        request_id="6fd88233-f8c6-4b08-b430-d9dfbaf3ba24",
        output="hello",
        provider="openai",
        model="gateway-default",
        tokens=GenerateTokenUsage(input_tokens=2, output_tokens=3, total_tokens=5),
        cost=GenerateCost(amount="0.0000060000", currency="USD"),
        routing_reason="configured_single_path",
        cache_status="miss",
        latency_ms=42,
    )

    assert response.model_dump(mode="json") == {
        "request_id": "6fd88233-f8c6-4b08-b430-d9dfbaf3ba24",
        "output": "hello",
        "provider": "openai",
        "model": "gateway-default",
        "tokens": {
            "input_tokens": 2,
            "output_tokens": 3,
            "total_tokens": 5,
        },
        "cost": {"amount": "0.0000060000", "currency": "USD"},
        "routing_reason": "configured_single_path",
        "cache_status": "miss",
        "latency_ms": 42,
    }


def test_chat_response_choice_requires_assistant_message() -> None:
    with pytest.raises(ValidationError):
        ChatCompletionChoice(
            index=0,
            message=ChatMessage(role=ChatRole.USER, content="hello"),
            finish_reason=FinishReason.STOP,
        )


@pytest.mark.parametrize("finish_reason", ["provider-specific", "tool_calls"])
def test_chat_response_choice_rejects_unsupported_finish_reason(finish_reason: str) -> None:
    with pytest.raises(ValidationError):
        ChatCompletionChoice(
            index=0,
            message=ChatMessage(role=ChatRole.ASSISTANT, content="hello"),
            finish_reason=finish_reason,
        )
