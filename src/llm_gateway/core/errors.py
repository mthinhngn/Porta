"""OpenAI-style HTTP error mapping."""

import logging
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from llm_gateway.core.logging import log_event
from llm_gateway.domain import ErrorDetail, ErrorResponse

logger = logging.getLogger("llm_gateway.errors")


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
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorDetail(
            message=message,
            type=error_type,
            param=param,
            code=code,
        )
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return error_response(
            status_code=exc.status_code,
            message=exc.message,
            error_type=exc.type,
            param=exc.param,
            code=exc.code,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request,
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
        )

    @app.exception_handler(HTTPException)
    async def handle_http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return error_response(
            status_code=exc.status_code,
            message=message,
            error_type="invalid_request_error" if exc.status_code < 500 else "server_error",
            code="http_error",
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
        )
