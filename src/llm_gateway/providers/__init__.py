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
from llm_gateway.providers.protocol import ChatCompletionProvider, ProviderContext
from llm_gateway.providers.testing import ProviderCall, ScriptedProvider

__all__ = [
    "ChatCompletionProvider",
    "ProviderAuthenticationError",
    "ProviderBadRequestError",
    "ProviderCall",
    "ProviderContext",
    "ProviderError",
    "ProviderRateLimitError",
    "ProviderResponseError",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
    "ScriptedProvider",
]
