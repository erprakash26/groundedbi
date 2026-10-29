"""Request context middleware: request ID, X-Request-ID and nosniff response headers, and the access log."""

import logging
import time

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.observability.context import parse_request_id, request_id_var

REQUEST_ID_HEADER = "X-Request-ID"
access_logger = logging.getLogger("app.access")


class RequestContextMiddleware:
    """Pure ASGI middleware (not BaseHTTPMiddleware).

    BaseHTTPMiddleware wraps the app in extra tasks and streams, which has caused
    contextvar and streaming problems; a plain ASGI callable just wraps `send`.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # lifespan and websocket messages pass straight through
            await self.app(scope, receive, send)
            return

        incoming = next((value for name, value in scope["headers"] if name == b"x-request-id"), None)
        request_id = parse_request_id(incoming.decode("latin-1") if incoming is not None else None)
        # Also stored on the request state: the 500 handler runs outside this middleware,
        # after the ContextVar below has been reset, and reads the ID from there.
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_var.set(request_id)
        status_code = 500  # if the app raises before responding, the client gets a 500
        start = time.perf_counter()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
                # Browsers must not guess a different type (e.g. run a JSON body as script).
                headers["X-Content-Type-Options"] = "nosniff"
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            access_logger.info(
                "request_completed",
                extra={
                    "event": "request_completed",
                    "method": scope["method"],
                    "path": scope["path"],  # ASGI path never includes the query string
                    "status_code": status_code,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                },
            )
            request_id_var.reset(token)
