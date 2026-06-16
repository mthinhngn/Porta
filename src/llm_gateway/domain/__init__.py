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
from llm_gateway.domain.phase2 import (
    AuthenticatedActor,
    GuardrailDecision,
    Phase2ResponseMetadata,
)

__all__ = [
    "AuthenticatedActor",
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
    "GuardrailDecision",
    "Phase2ResponseMetadata",
    "TokenUsage",
]
