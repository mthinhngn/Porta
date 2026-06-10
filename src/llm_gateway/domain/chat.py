"""Narrow non-streaming chat completion contracts."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from llm_gateway.domain.base import ContractModel


class ChatRole(StrEnum):
    DEVELOPER = "developer"
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ChatMessage(ContractModel):
    role: ChatRole
    content: str
    name: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    tool_call_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None


StopSequence = Annotated[str, Field(min_length=1, max_length=1024)]


class ChatCompletionRequest(ContractModel):
    model: Annotated[str, Field(min_length=1, max_length=255)]
    messages: Annotated[list[ChatMessage], Field(min_length=1)]
    temperature: Annotated[float, Field(ge=0.0, le=2.0)] | None = None
    top_p: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    n: Annotated[int, Field(ge=1, le=16)] | None = None
    max_tokens: Annotated[int, Field(ge=1)] | None = None
    max_completion_tokens: Annotated[int, Field(ge=1)] | None = None
    presence_penalty: Annotated[float, Field(ge=-2.0, le=2.0)] | None = None
    frequency_penalty: Annotated[float, Field(ge=-2.0, le=2.0)] | None = None
    stop: StopSequence | Annotated[list[StopSequence], Field(min_length=1, max_length=4)] | None = (
        None
    )
    seed: int | None = None
    user: Annotated[str, Field(min_length=1, max_length=255)] | None = None


class TokenUsage(ContractModel):
    prompt_tokens: Annotated[int, Field(ge=0)]
    completion_tokens: Annotated[int, Field(ge=0)]
    total_tokens: Annotated[int, Field(ge=0)]


class ChatCompletionChoice(ContractModel):
    index: Annotated[int, Field(ge=0)]
    message: ChatMessage
    finish_reason: str | None = None


class ChatCompletionResponse(ContractModel):
    id: Annotated[str, Field(min_length=1, max_length=255)]
    object: Literal["chat.completion"] = "chat.completion"
    created: Annotated[int, Field(ge=0)]
    model: Annotated[str, Field(min_length=1, max_length=255)]
    choices: Annotated[list[ChatCompletionChoice], Field(min_length=1)]
    usage: TokenUsage
