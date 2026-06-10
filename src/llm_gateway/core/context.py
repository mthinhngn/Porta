"""Request-scoped operational context."""

from contextvars import ContextVar, Token

correlation_id_context: ContextVar[str | None] = ContextVar(
    "correlation_id",
    default=None,
)


def bind_correlation_id(correlation_id: str) -> Token[str | None]:
    return correlation_id_context.set(correlation_id)


def reset_correlation_id(token: Token[str | None]) -> None:
    correlation_id_context.reset(token)
