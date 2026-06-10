"""Normalized provider error taxonomy."""

from collections.abc import Mapping
from typing import Any


class ProviderError(Exception):
    """Base provider failure safe for orchestration-level handling."""

    default_code = "provider_error"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        provider_request_id: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        self.status_code = status_code
        self.provider_request_id = provider_request_id
        self.details = dict(details or {})


class ProviderBadRequestError(ProviderError):
    default_code = "provider_bad_request"


class ProviderAuthenticationError(ProviderError):
    default_code = "provider_authentication_error"


class ProviderRateLimitError(ProviderError):
    default_code = "provider_rate_limit"
    retryable = True


class ProviderTimeoutError(ProviderError):
    default_code = "provider_timeout"
    retryable = True


class ProviderUnavailableError(ProviderError):
    default_code = "provider_unavailable"
    retryable = True


class ProviderResponseError(ProviderError):
    default_code = "provider_invalid_response"
