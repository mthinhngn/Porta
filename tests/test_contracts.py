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
    TokenUsage,
)


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
                "stream": True,
            }
        )


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
    assert response.usage.total_tokens == 2
    assert error.model_dump() == {
        "error": {
            "message": "Invalid request.",
            "type": "invalid_request_error",
            "param": "model",
            "code": "validation_error",
        }
    }
