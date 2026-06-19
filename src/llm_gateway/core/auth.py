"""Gateway API-key authentication for Phase 2."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Final

from fastapi import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from llm_gateway.core.config import Settings
from llm_gateway.core.errors import error_response
from llm_gateway.core.metrics import record_auth_event, record_generate_event
from llm_gateway.domain import AuthenticatedActor

AUTHORIZATION_HEADER_NAME: Final[bytes] = b"authorization"
BEARER_PREFIX: Final[str] = "bearer "


@dataclass(frozen=True, slots=True)
class GatewayAuthResult:
    actor: AuthenticatedActor | None
    status_code: int | None = None
    message: str | None = None
    code: str | None = None


def _authorization_value(scope: Scope) -> str | None:
    values = [
        value
        for name, value in scope.get("headers", [])
        if name.lower() == AUTHORIZATION_HEADER_NAME
    ]
    if len(values) != 1:
        return None
    if not isinstance(values[0], bytes):
        return None
    try:
        return values[0].decode("ascii")
    except UnicodeDecodeError:
        return None


def _bearer_token(value: str | None) -> str | None:
    if value is None:
        return None
    prefix, _, token = value.partition(" ")
    if prefix.casefold() != "bearer" or not token:
        return None
    return token


def authenticate_gateway_request(
    *,
    settings: Settings,
    authorization_value: str | None,
) -> GatewayAuthResult:
    token = _bearer_token(authorization_value)
    if token is None:
        return GatewayAuthResult(
            actor=None,
            status_code=401,
            message="Authentication required.",
            code="authentication_error",
        )

    for candidate in settings.gateway_api_keys:
        if not secrets.compare_digest(candidate.key, token):
            continue
        if not candidate.enabled:
            return GatewayAuthResult(
                actor=None,
                status_code=403,
                message="API key is disabled.",
                code="authentication_error",
            )
        return GatewayAuthResult(
            actor=AuthenticatedActor(
                actor_id=candidate.actor_id,
                api_key_id=candidate.api_key_id,
                enabled=True,
                is_admin=candidate.is_admin,
                request_quota_limit=candidate.request_quota_limit,
                allowed_providers=candidate.allowed_providers,
            )
        )

    return GatewayAuthResult(
        actor=None,
        status_code=401,
        message="Authentication required.",
        code="authentication_error",
    )


class GatewayAuthMiddleware:
    """Require a valid gateway API key for the generate endpoint."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path")
        protected_path = path == "/v1/generate" or (
            isinstance(path, str) and path.startswith("/v1/analytics/")
        )
        if scope["type"] != "http" or not protected_path:
            await self.app(scope, receive, send)
            return

        app = scope.get("app")
        settings = getattr(getattr(app, "state", None), "settings", None)
        if not isinstance(settings, Settings):
            record_auth_event(result="unavailable", error_code="service_unavailable")
            record_generate_event(
                stage="auth",
                result="unavailable",
                error_code="service_unavailable",
            )
            response = error_response(
                status_code=503,
                message="Authentication is unavailable.",
                error_type="server_error",
                code="service_unavailable",
            )
            await response(scope, receive, send)
            return

        result = authenticate_gateway_request(
            settings=settings,
            authorization_value=_authorization_value(scope),
        )
        if result.actor is None:
            record_auth_event(result="failure", error_code=result.code or "authentication_error")
            record_generate_event(
                stage="auth",
                result="failure",
                error_code=result.code or "authentication_error",
            )
            response = error_response(
                status_code=result.status_code or 401,
                message=result.message or "Authentication required.",
                error_type="invalid_request_error",
                code=result.code or "authentication_error",
            )
            await response(scope, receive, send)
            return

        record_auth_event(result="success")
        record_generate_event(stage="auth", result="success")
        scope.setdefault("state", {})["authenticated_actor"] = result.actor
        await self.app(scope, receive, send)


def authenticated_actor(request: Request) -> AuthenticatedActor:
    actor = getattr(request.state, "authenticated_actor", None)
    if not isinstance(actor, AuthenticatedActor):
        raise RuntimeError("authenticated actor is unavailable")
    return actor
