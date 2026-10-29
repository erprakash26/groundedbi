"""HTTP rules for public deployment: response caching and a request body size limit.

Both are pure ASGI middleware, like RequestContextMiddleware, and sit inside it so their
responses still get a request ID, the access log line and nosniff.
"""

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.errors import error_response

# The data only changes when the database is re-seeded, so successful reads can be shared:
# browsers keep them 5 minutes (max-age), a CDN 1 hour (s-maxage).
CACHEABLE = "public, max-age=300, s-maxage=3600"
NOT_CACHEABLE = "no-store"

# A question is at most 500 characters; 16 KB leaves room for JSON escaping and nothing else.
MAX_BODY_BYTES = 16 * 1024


def cache_control_for(method: str, path: str, status_code: int) -> str | None:
    """The Cache-Control value for an API response, or None to leave the response alone.

    Only successful GETs of read-only data are cacheable. POST /ask never is: its answer
    carries a request ID and trace for that one request. Errors never are, so a temporary
    503 can't be served from a cache after the database is back.
    """
    is_api = path == "/ask" or path.startswith(("/ask/", "/analytics/"))
    if not is_api:
        return None  # pages, /static, /health and /docs keep their own defaults
    if method == "GET" and status_code == 200 and (path.startswith("/analytics/") or path == "/ask/examples"):
        return CACHEABLE
    return NOT_CACHEABLE


class CacheControlMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_cache_control(message: Message) -> None:
            if message["type"] == "http.response.start":
                value = cache_control_for(scope["method"], scope["path"], message["status"])
                if value:
                    MutableHeaders(scope=message)["Cache-Control"] = value
            await send(message)

        await self.app(scope, receive, send_with_cache_control)


class BodySizeLimitMiddleware:
    """Reject request bodies over max_bytes with a 413, before any route parses them.

    Two checks, because Content-Length is optional (a chunked request has none) and is only
    what the client claims:
      1. a declared Content-Length over the limit is refused without reading anything;
      2. otherwise the body is read here, counting the bytes actually received, and handed
         on to the app only if it fits. It can't simply raise partway through the app's own
         read: FastAPI would catch that while parsing the body and answer 400.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = Headers(scope=scope).get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(scope, receive, send)
            return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return  # the client has gone; there is nobody to answer
            body += message.get("body", b"")
            if len(body) > self.max_bytes:
                await self._reject(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay_body() -> Message:
            # The app gets the whole body as one message, then the real receive (for disconnects).
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay_body, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        message = f"The request body is larger than {self.max_bytes // 1024} KB."
        response = error_response(Request(scope), 413, "payload_too_large", message)
        await response(scope, receive, send)
