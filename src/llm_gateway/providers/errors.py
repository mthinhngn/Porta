"""Normalized provider error taxonomy."""

import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

SAFE_DETAIL_KEYS = frozenset({"error_code", "error_type", "retry_after", "status_code"})
SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?i)(?:authorization|bearer\s+\S+|api[_-]?key|credential|password|"
    r"prompt|completion|secret|token|sk-(?:ant-)?[A-Za-z0-9_-]{8,})"
)


def _safe_message(message: str) -> str:
    if len(message) > 512 or SENSITIVE_VALUE_PATTERN.search(message):
        return "Provider request failed."
    return message


def _safe_details(details: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if details is None:
        return MappingProxyType({})

    safe: dict[str, Any] = {}
    for key, value in details.items():
        if key not in SAFE_DETAIL_KEYS or not isinstance(value, str | int | float | bool | None):
            continue
        if isinstance(value, str) and SENSITIVE_VALUE_PATTERN.search(value):
            continue
        safe[key] = value
    return MappingProxyType(safe)


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
        self.message = _safe_message(message)
        super().__init__(self.message)
        self.code = code or self.default_code
        self.status_code = status_code
        self.provider_request_id = provider_request_id
        self.details = _safe_details(details)


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
