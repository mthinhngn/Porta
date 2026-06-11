import asyncio
import re
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.types import Message, Receive, Scope, Send

from llm_gateway.core.context import correlation_id_context
from llm_gateway.core.middleware import CorrelationIdMiddleware


def _http_scope(headers: list[tuple[bytes, bytes]], path: str = "/private") -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
        "state": {},
    }


async def _receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


def _run_middleware(
    app: Callable[[Scope, Receive, Send], Awaitable[None]],
    scope: Scope,
) -> list[Message]:
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    asyncio.run(CorrelationIdMiddleware(app)(scope, _receive, send))
    return sent


def test_duplicate_inbound_ids_are_replaced_and_response_header_is_unique() -> None:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"x-request-id", b"application-value"),
                    (b"X-Request-ID", b"second-application-value"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    scope = _http_scope(
        [
            (b"x-request-id", b"first-client-value"),
            (b"X-Request-ID", b"second-client-value"),
        ]
    )
    messages = _run_middleware(app, scope)
    response_start = messages[0]
    response_ids = [
        value for name, value in response_start["headers"] if name.lower() == b"x-request-id"
    ]

    assert len(response_ids) == 1
    assert re.fullmatch(rb"[0-9a-f]{32}", response_ids[0])
    assert scope["state"]["correlation_id"] == response_ids[0].decode("ascii")


def test_non_ascii_inbound_id_is_replaced() -> None:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    messages = _run_middleware(app, _http_scope([(b"x-request-id", b"\xffvalid")]))
    response_id = messages[0]["headers"][0][1]

    assert re.fullmatch(rb"[0-9a-f]{32}", response_id)


def test_credential_shaped_inbound_id_is_replaced() -> None:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    messages = _run_middleware(
        app,
        _http_scope([(b"x-request-id", b"sk-provider-secret-value")]),
    )
    response_id = messages[0]["headers"][0][1]

    assert response_id != b"sk-provider-secret-value"
    assert re.fullmatch(rb"[0-9a-f]{32}", response_id)


def test_logs_only_trusted_route_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def capture_log_event(*args: Any, **fields: Any) -> None:
        captured.update(fields)

    monkeypatch.setattr("llm_gateway.core.middleware.log_event", capture_log_event)

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        scope["route"] = SimpleNamespace(path="/items/{item_id}")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    _run_middleware(
        app,
        _http_scope([], path="/items/private-prompt?authorization=Bearer-secret"),
    )

    assert captured["path"] == "/items/{item_id}"
    assert "private-prompt" not in repr(captured)
    assert "Bearer-secret" not in repr(captured)


def test_unknown_path_and_request_metadata_are_not_logged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def capture_log_event(*args: Any, **fields: Any) -> None:
        captured.update(fields)

    monkeypatch.setattr("llm_gateway.core.middleware.log_event", capture_log_event)

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    scope = _http_scope(
        [(b"authorization", b"Bearer private-token")],
        path="/unknown/private-prompt",
    )
    scope["query_string"] = b"completion=private-completion"
    _run_middleware(app, scope)

    assert "path" not in captured
    assert "private" not in repr(captured)
    assert set(captured) == {
        "method",
        "status_code",
        "duration_ms",
        "correlation_id",
    }


def test_context_is_reset_when_access_logging_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_log_event(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("logging failed")

    monkeypatch.setattr("llm_gateway.core.middleware.log_event", fail_log_event)

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    with pytest.raises(RuntimeError, match="logging failed"):
        _run_middleware(app, _http_scope([]))

    assert correlation_id_context.get() is None
