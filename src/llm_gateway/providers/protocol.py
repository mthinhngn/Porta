"""Typed asynchronous boundary implemented by provider adapters."""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from llm_gateway.domain import ChatCompletionRequest, ChatCompletionResponse


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
