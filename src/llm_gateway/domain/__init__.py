"""Public, transport-neutral gateway contracts."""

from llm_gateway.domain.chat import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    ChatRole,
    TokenUsage,
)
from llm_gateway.domain.errors import ErrorDetail, ErrorResponse
from llm_gateway.domain.generate import (
    GenerateCost,
    GenerateRequest,
    GenerateResponse,
    GenerateTokenUsage,
)

__all__ = [
    "ChatCompletionChoice",
    "ChatCompletionRequest",
    "ChatCompletionResponse",
    "ChatMessage",
    "ChatRole",
    "ErrorDetail",
    "ErrorResponse",
    "GenerateCost",
    "GenerateRequest",
    "GenerateResponse",
    "GenerateTokenUsage",
    "TokenUsage",
]
