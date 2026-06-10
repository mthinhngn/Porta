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

__all__ = [
    "ChatCompletionChoice",
    "ChatCompletionRequest",
    "ChatCompletionResponse",
    "ChatMessage",
    "ChatRole",
    "ErrorDetail",
    "ErrorResponse",
    "TokenUsage",
]
