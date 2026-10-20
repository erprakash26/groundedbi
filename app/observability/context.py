"""Per-request context: the current request ID, readable from any code without passing it around."""

import re
from contextvars import ContextVar
from uuid import uuid4

# A ContextVar is isolated per request: each request runs in its own asyncio task (and
# FastAPI copies the context into threadpool threads for sync code), so concurrent
# requests never see each other's ID. None outside a request.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Letters, digits, _ and - only. Anything else (spaces, newlines, quotes) could be used to
# forge fake log lines or inject into whatever reads the logs.
_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")


def get_request_id() -> str | None:
    return request_id_var.get()


def parse_request_id(header_value: str | None) -> str:
    """Reuse a well-formed client-supplied ID, otherwise generate a new one.

    fullmatch, not match with "^...$": in Python "$" also matches just before a trailing
    newline, so "abc\\n" would pass a "^...$" check.
    """
    if header_value is not None and _REQUEST_ID_PATTERN.fullmatch(header_value):
        return header_value
    return uuid4().hex
