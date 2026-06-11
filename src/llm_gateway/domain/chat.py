"""Narrow non-streaming chat completion contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from llm_gateway.domain.base import ContractModel


class ChatRole(StrEnum):
    DEVELOPER = "developer"
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class FinishReason(StrEnum):
    STOP = "stop"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"


class ChatMessage(ContractModel):
    role: ChatRole
    content: str
    name: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    tool_call_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None

    @model_validator(mode="after")
    def validate_tool_call_id(self) -> ChatMessage:
        if self.role is ChatRole.TOOL and self.tool_call_id is None:
            raise ValueError("tool messages require tool_call_id")
        if self.role is not ChatRole.TOOL and self.tool_call_id is not None:
            raise ValueError("tool_call_id is only valid for tool messages")
        return self


StopSequence = Annotated[str, Field(min_length=1, max_length=1024)]


class ChatCompletionRequest(ContractModel):
    model: Annotated[str, Field(min_length=1, max_length=255)]
    messages: Annotated[list[ChatMessage], Field(min_length=1)]
    stream: Literal[False] = False
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

    @model_validator(mode="after")
    def validate_total_tokens(self) -> TokenUsage:
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("total_tokens must equal prompt_tokens + completion_tokens")
        return self


class ChatCompletionChoice(ContractModel):
    index: Annotated[int, Field(ge=0)]
    message: ChatMessage
    finish_reason: FinishReason | None = None

    @model_validator(mode="after")
    def validate_assistant_message(self) -> ChatCompletionChoice:
        if self.message.role is not ChatRole.ASSISTANT:
            raise ValueError("response choices require an assistant message")
        return self


class ChatCompletionResponse(ContractModel):
    id: Annotated[str, Field(min_length=1, max_length=255)]
    object: Literal["chat.completion"] = "chat.completion"
    created: Annotated[int, Field(ge=0)]
    model: Annotated[str, Field(min_length=1, max_length=255)]
    choices: Annotated[list[ChatCompletionChoice], Field(min_length=1)]
    usage: TokenUsage
