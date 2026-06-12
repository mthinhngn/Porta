"""Provider abstraction and provider-facing errors."""

from llm_gateway.providers.errors import (
    ProviderAuthenticationError,
    ProviderBadRequestError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from llm_gateway.providers.openai_responses import OpenAIResponsesProvider
from llm_gateway.providers.protocol import (
    ChatCompletionProvider,
    GenerateProvider,
    GenerateProviderContext,
    GenerateProviderResult,
    ProviderContext,
    ProviderTokenUsage,
)
from llm_gateway.providers.testing import ProviderCall, ScriptedProvider

__all__ = [
    "ChatCompletionProvider",
    "GenerateProvider",
    "GenerateProviderContext",
    "GenerateProviderResult",
    "OpenAIResponsesProvider",
    "ProviderAuthenticationError",
    "ProviderBadRequestError",
    "ProviderCall",
    "ProviderContext",
    "ProviderError",
    "ProviderRateLimitError",
    "ProviderResponseError",
    "ProviderTimeoutError",
    "ProviderTokenUsage",
    "ProviderUnavailableError",
    "ScriptedProvider",
]
