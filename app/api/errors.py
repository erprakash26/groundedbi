"""One error shape for every failure:

    {"error": {"code": "...", "message": "...", "request_id": "...", "details": [...]}}

"details" is only present for validation errors. Clients can branch on the stable "code"
and quote "request_id" when reporting a problem; it matches the X-Request-ID header and
the request_id field in the server logs.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.observability.middleware import REQUEST_ID_HEADER

logger = logging.getLogger("app.errors")

# Codes for Starlette's own HTTP errors (e.g. the router's 404/405). Our code raises APIError instead.
_HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}


class APIError(Exception):
    """An expected error with a client-facing status, code and message.

    The raiser decides the code, so a 503 from the database and a future 503 from the
    LLM provider can carry different codes.
    """

    def __init__(self, status_code: int, code: str, message: str, details: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def _request_id(request: Request) -> str | None:
    # Set by RequestContextMiddleware; read from request.state (not the ContextVar) because
    # the 500 handler runs after the middleware has already reset the ContextVar.
    return getattr(request.state, "request_id", None)


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = _request_id(request)
    body: dict[str, Any] = {"code": code, "message": message, "request_id": request_id}
    if details is not None:
        body["details"] = details
    headers = dict(headers or {})
    if request_id:
        # Needed for the 500 path, which bypasses the middleware's send wrapper.
        # For every other error the middleware sets the same value again.
        headers[REQUEST_ID_HEADER] = request_id
    return JSONResponse({"error": body}, status_code=status_code, headers=headers)


async def handle_api_error(request: Request, exc: APIError) -> JSONResponse:
    return error_response(request, exc.status_code, exc.code, exc.message, exc.details)


async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Keep where and why; drop "input" (it would echo injection strings back) and "ctx".
    details = [
        {
            "location": error["loc"][0] if error["loc"] else None,
            "field": ".".join(str(part) for part in error["loc"][1:]),
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors()
    ]
    return error_response(request, 422, "validation_error", "Request validation failed.", details)


async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _HTTP_CODES.get(exc.status_code, "http_error")
    # exc.headers keeps e.g. the Allow header on a 405.
    return error_response(request, exc.status_code, code, str(exc.detail), headers=exc.headers)


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Last resort: full traceback to the log, a generic message to the client."""
    logger.error(
        "unhandled_exception",
        exc_info=(type(exc), exc, exc.__traceback__),
        extra={"event": "unhandled_exception", "request_id": _request_id(request),
               "method": request.method, "path": request.url.path},
    )
    return error_response(request, 500, "internal_error", "An internal error occurred.")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(APIError, handle_api_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    # Starlette's class, not FastAPI's subclass, so router 404/405 are caught too.
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    # Exception goes to Starlette's outermost ServerErrorMiddleware as its 500 handler.
    app.add_exception_handler(Exception, handle_unexpected_error)
