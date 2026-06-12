"""Typed asynchronous boundary implemented by provider adapters."""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from llm_gateway.domain import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    GenerateRequest,
)


def _freeze_metadata(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_metadata(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze_metadata(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_freeze_metadata(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class ProviderContext:
    gateway_request_id: UUID
    correlation_id: str
    provider_name: str
    model_name: str
    timeout_seconds: float
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))


@runtime_checkable
class ChatCompletionProvider(Protocol):
    @property
    def name(self) -> str:
        """Stable configured provider name."""
        ...

    async def complete(
        self,
        request: ChatCompletionRequest,
        context: ProviderContext,
    ) -> ChatCompletionResponse:
        """Return a normalized completion or raise ProviderError."""
        ...


@dataclass(frozen=True, slots=True)
class GenerateProviderContext:
    gateway_request_id: UUID
    correlation_id: str
    provider_name: str
    model_name: str
    timeout_seconds: float
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class ProviderTokenUsage:
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    total_tokens: int

    def __post_init__(self) -> None:
        values = (
            self.input_tokens,
            self.cached_input_tokens,
            self.output_tokens,
            self.total_tokens,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values
        ):
            raise ValueError("provider token usage values must be non-negative integers")
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached_input_tokens must not exceed input_tokens")
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must equal input_tokens + output_tokens")


@dataclass(frozen=True, slots=True)
class GenerateProviderResult:
    output: str
    usage: ProviderTokenUsage
    provider_request_id: str | None = None
    cache_status: str = "miss"


@runtime_checkable
class GenerateProvider(Protocol):
    @property
    def name(self) -> str:
        """Stable configured provider name."""
        ...

    async def generate(
        self,
        request: GenerateRequest,
        context: GenerateProviderContext,
    ) -> GenerateProviderResult:
        """Return a normalized generation result or raise ProviderError."""
        ...
