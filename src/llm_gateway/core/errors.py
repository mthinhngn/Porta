"""OpenAI-style HTTP error mapping."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from llm_gateway.core.logging import log_event, sanitize_log_message
from llm_gateway.domain import ErrorDetail, ErrorResponse

logger = logging.getLogger("llm_gateway.errors")
SAFE_HTTP_ERROR_HEADERS = frozenset({"allow", "retry-after"})


@dataclass(frozen=True, slots=True)
class ApiError(Exception):
    message: str
    type: str
    status_code: int
    param: str | None = None
    code: str | int | None = None


def error_response(
    *,
    status_code: int,
    message: str,
    error_type: str,
    param: str | None = None,
    code: str | int | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorDetail(
            message=message,
            type=error_type,
            param=param,
            code=code,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(),
        headers=headers,
    )


def _safe_http_detail(detail: object) -> str:
    if not isinstance(detail, str):
        return "Request failed."
    return detail if sanitize_log_message(detail) == detail else "Request failed."


def _safe_http_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    if headers is None:
        return {}

    safe: dict[str, str] = {}
    for name, value in headers.items():
        normalized_name = name.casefold()
        if normalized_name not in SAFE_HTTP_ERROR_HEADERS:
            continue
        if "\r" in value or "\n" in value or sanitize_log_message(value) != value:
            continue
        safe[name] = value
    return safe


def _correlation_header(request: Request) -> dict[str, str]:
    correlation_id = getattr(request.state, "correlation_id", None)
    if not isinstance(correlation_id, str):
        return {}
    settings = getattr(request.app.state, "settings", None)
    header_name = getattr(settings, "correlation_id_header", "X-Request-ID")
    return {header_name: correlation_id}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(
            status_code=exc.status_code,
            message=exc.message,
            error_type=exc.type,
            param=exc.param,
            code=exc.code,
            headers=_correlation_header(request),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        first_error = exc.errors()[0] if exc.errors() else {}
        location = first_error.get("loc", ())
        param = ".".join(str(part) for part in location if part not in {"body", "query"})
        return error_response(
            status_code=400,
            message="Request validation failed.",
            error_type="invalid_request_error",
            param=param or None,
            code="validation_error",
            headers=_correlation_header(request),
        )

    @app.exception_handler(HTTPException)
    async def handle_http_error(request: Request, exc: HTTPException) -> JSONResponse:
        headers = _safe_http_headers(exc.headers)
        headers.update(_correlation_header(request))
        return error_response(
            status_code=exc.status_code,
            message=_safe_http_detail(exc.detail),
            error_type="invalid_request_error" if exc.status_code < 500 else "server_error",
            code="http_error",
            headers=headers,
        )

    @app.exception_handler(Exception)
    async def handle_internal_error(request: Request, _exc: Exception) -> JSONResponse:
        log_event(
            logger,
            logging.ERROR,
            "unhandled_application_error",
            correlation_id=getattr(request.state, "correlation_id", None),
            error_code="internal_error",
        )
        return error_response(
            status_code=500,
            message="An internal server error occurred.",
            error_type="server_error",
            code="internal_error",
            headers=_correlation_header(request),
        )
