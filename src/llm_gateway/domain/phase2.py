"""Phase 2 transport-neutral contract skeletons."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from llm_gateway.domain.base import ContractModel


class AuthenticatedActor(ContractModel):
    actor_id: UUID
    api_key_id: UUID
    enabled: bool
    request_quota_limit: Annotated[int | None, Field(ge=1)] = None
    allowed_providers: tuple[Literal["openai", "anthropic", "gemini"], ...] | None = None


class GuardrailDecision(ContractModel):
    outcome: Literal["allow", "block"]
    reason_code: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    version: Annotated[str, Field(min_length=1, max_length=64)]


class Phase2ResponseMetadata(ContractModel):
    served_from_cache: bool
    attempt_count: Annotated[int, Field(ge=0)]
    provider: Annotated[str, Field(min_length=1, max_length=128)]
