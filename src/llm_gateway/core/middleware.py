"""HTTP middleware for safe request correlation and access logs."""

import logging
import re
from time import perf_counter
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from llm_gateway.core.context import bind_correlation_id, reset_correlation_id
from llm_gateway.core.logging import log_event

CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
logger = logging.getLogger("llm_gateway.requests")


def normalize_correlation_id(value: str | None) -> str:
    if value is not None and CORRELATION_ID_PATTERN.fullmatch(value):
        return value
    return uuid4().hex


class CorrelationIdMiddleware:
    """Bind a validated correlation ID and return it on every HTTP response."""

    def __init__(self, app: ASGIApp, *, header_name: str = "X-Request-ID") -> None:
        self.app = app
        self.header_name = header_name
        self.header_name_bytes = header_name.lower().encode("ascii")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        raw_value = headers.get(self.header_name_bytes)
        inbound = raw_value.decode("ascii", errors="ignore") if raw_value else None
        correlation_id = normalize_correlation_id(inbound)
        scope.setdefault("state", {})["correlation_id"] = correlation_id
        token = bind_correlation_id(correlation_id)
        started_at = perf_counter()
        status_code = 500

        async def send_with_correlation(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = list(message.get("headers", []))
                response_headers.append((self.header_name_bytes, correlation_id.encode("ascii")))
                message["headers"] = response_headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_correlation)
        finally:
            log_event(
                logger,
                logging.INFO,
                "http_request_completed",
                method=scope["method"],
                path=scope["path"],
                status_code=status_code,
                duration_ms=round((perf_counter() - started_at) * 1000, 3),
                correlation_id=correlation_id,
            )
            reset_correlation_id(token)
