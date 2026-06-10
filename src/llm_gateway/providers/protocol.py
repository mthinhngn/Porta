"""Typed asynchronous boundary implemented by provider adapters."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from llm_gateway.domain import ChatCompletionRequest, ChatCompletionResponse


@dataclass(frozen=True, slots=True)
class ProviderContext:
    gateway_request_id: UUID
    correlation_id: str
    provider_name: str
    model_name: str
    timeout_seconds: float
    metadata: Mapping[str, Any] = field(default_factory=dict)


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
