"""HTTP middleware for safe request correlation and access logs."""

import logging
import re
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from llm_gateway.core.context import bind_correlation_id, reset_correlation_id
from llm_gateway.core.logging import log_event, sanitize_log_message
from llm_gateway.core.metrics import record_http_request

CORRELATION_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")
logger = logging.getLogger("llm_gateway.requests")


def normalize_correlation_id(value: str | None) -> str:
    if value is not None and CORRELATION_ID_PATTERN.fullmatch(value):
        parsed = UUID(value)
        if parsed.version == 4 and sanitize_log_message(value) == value:
            return parsed.hex
    return uuid4().hex


def _inbound_correlation_id(scope: Scope, header_name: bytes) -> str:
    values = [value for name, value in scope.get("headers", []) if name.lower() == header_name]
    if len(values) != 1:
        return normalize_correlation_id(None)
    try:
        value = values[0].decode("ascii")
    except UnicodeDecodeError:
        return normalize_correlation_id(None)
    return normalize_correlation_id(value)


def _trusted_route_template(scope: Scope) -> str | None:
    route: Any = scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path.startswith("/") and "?" not in path:
        return path
    return None


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

        correlation_id = _inbound_correlation_id(scope, self.header_name_bytes)
        scope.setdefault("state", {})["correlation_id"] = correlation_id
        token = bind_correlation_id(correlation_id)
        started_at = perf_counter()
        status_code = 500

        async def send_with_correlation(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != self.header_name_bytes
                ]
                response_headers.append((self.header_name_bytes, correlation_id.encode("ascii")))
                message["headers"] = response_headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_correlation)
        finally:
            try:
                duration_seconds = perf_counter() - started_at
                route_template = _trusted_route_template(scope)
                record_http_request(
                    method=scope["method"],
                    route_template=route_template,
                    status_code=status_code,
                    duration_seconds=duration_seconds,
                )
                fields: dict[str, Any] = {
                    "method": scope["method"],
                    "status_code": status_code,
                    "duration_ms": round(duration_seconds * 1000, 3),
                    "correlation_id": correlation_id,
                }
                if route_template is not None:
                    fields["path"] = route_template
                log_event(
                    logger,
                    logging.INFO,
                    "http_request_completed",
                    **fields,
                )
            finally:
                reset_correlation_id(token)
