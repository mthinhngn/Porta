"""Transport-neutral contracts for the R1 generate path."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import Field, model_validator

from llm_gateway.domain.base import ContractModel


class GenerateRequest(ContractModel):
    model: Annotated[str, Field(min_length=1, max_length=255)]
    input: Annotated[str, Field(min_length=1, max_length=32768)]
    temperature: Annotated[float, Field(ge=0.0, le=2.0)] | None = None
    top_p: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    max_output_tokens: Annotated[int, Field(ge=1)] | None = None


class GenerateTokenUsage(ContractModel):
    input_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]
    total_tokens: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def validate_total_tokens(self) -> GenerateTokenUsage:
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must equal input_tokens + output_tokens")
        return self


class GenerateCost(ContractModel):
    amount: Annotated[Decimal, Field(ge=0)]
    currency: Annotated[str, Field(min_length=3, max_length=3)]


class GenerateResponse(ContractModel):
    request_id: UUID
    output: str
    provider: Annotated[str, Field(min_length=1, max_length=128)]
    model: Annotated[str, Field(min_length=1, max_length=255)]
    tokens: GenerateTokenUsage
    cost: GenerateCost
    routing_reason: Annotated[str, Field(min_length=1, max_length=255)]
    cache_status: Annotated[str, Field(min_length=1, max_length=64)]
    latency_ms: Annotated[int, Field(ge=0)]
