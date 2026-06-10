"""OpenAI-style public error contracts."""

from typing import Annotated

from pydantic import Field

from llm_gateway.domain.base import ContractModel


class ErrorDetail(ContractModel):
    message: Annotated[str, Field(min_length=1)]
    type: Annotated[str, Field(min_length=1, max_length=128)]
    param: str | None = None
    code: str | int | None = None


class ErrorResponse(ContractModel):
    error: ErrorDetail
