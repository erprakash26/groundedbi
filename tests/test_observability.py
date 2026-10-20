import asyncio
import io
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.observability.context import get_request_id, parse_request_id
from app.observability.logging import JsonFormatter, configure_logging
from app.observability.middleware import RequestContextMiddleware

HEX_ID = re.compile(r"[0-9a-f]{32}")
endpoint_logger = logging.getLogger("tests.endpoint")


@pytest.fixture(scope="module")
def client():
    """A fresh app with one test-only route that logs from inside the endpoint."""
    app = create_app()

    @app.get("/test-log")
    def write_log() -> dict[str, str | None]:
        # A sync endpoint: FastAPI runs it in a threadpool thread.
        endpoint_logger.info("inside_endpoint", extra={"event": "inside_endpoint"})
        return {"request_id_seen": get_request_id()}

    return TestClient(app)


# --- parse_request_id ---


@pytest.mark.parametrize("value", ["abc", "A-b_9", "x" * 64, "3f2a9c0d1e2b4c5d6e7f8a9b0c1d2e3f"])
def test_valid_request_id_is_reused(value):
    assert parse_request_id(value) == value


@pytest.mark.parametrize(
    "value",
    [None, "", "abc\n", "abc\r\nFAKE LOG LINE", "x" * 65, "x" * 100, "a b", "a;b", "é", "٢"],
)
def test_invalid_request_id_is_replaced(value):
    replacement = parse_request_id(value)
    assert replacement != value and HEX_ID.fullmatch(replacement)


# --- X-Request-ID header ---


def test_response_always_has_request_id(client):
    response = client.get("/health")
    assert HEX_ID.fullmatch(response.headers["X-Request-ID"])


def test_valid_incoming_request_id_is_echoed(client):
    assert client.get("/health", headers={"X-Request-ID": "client-id_42"}).headers["X-Request-ID"] == "client-id_42"


def test_100_character_request_id_is_replaced(client):
    response = client.get("/health", headers={"X-Request-ID": "x" * 100})
    assert HEX_ID.fullmatch(response.headers["X-Request-ID"])


def test_request_id_with_newline_is_replaced_by_middleware():
    """Sent at the raw ASGI level: HTTP clients refuse to send a header containing a newline."""
    sent = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "GET", "path": "/", "headers": [(b"x-request-id", b"abc\n")]}
    asyncio.run(RequestContextMiddleware(app)(scope, receive, send))
    headers = dict(sent[0]["headers"])
    assert HEX_ID.fullmatch(headers[b"x-request-id"].decode())


def test_two_requests_get_different_ids(client):
    assert client.get("/health").headers["X-Request-ID"] != client.get("/health").headers["X-Request-ID"]


# --- access log ---


def test_access_log_line(client, log_lines):
    response = client.get("/health?secret=do-not-log", headers={"X-Request-ID": "access-test", "User-Agent": "spy"})
    [line] = [entry for entry in log_lines() if entry.get("event") == "request_completed"]
    assert line["request_id"] == response.headers["X-Request-ID"] == "access-test"
    assert line["logger"] == "app.access" and line["level"] == "INFO"
    assert (line["method"], line["path"], line["status_code"]) == ("GET", "/health", 200)
    assert isinstance(line["duration_ms"], float) and line["duration_ms"] >= 0
    assert line["duration_ms"] == round(line["duration_ms"], 1)
    raw = json.dumps(line)
    assert "do-not-log" not in raw and "spy" not in raw  # no query string, no headers


def test_access_log_records_error_status(client, log_lines):
    client.get("/does-not-exist")
    [line] = [entry for entry in log_lines() if entry.get("event") == "request_completed"]
    assert line["status_code"] == 404


def test_request_id_reaches_logs_written_inside_endpoint(client, log_lines):
    response = client.get("/test-log", headers={"X-Request-ID": "inside-test"})
    assert response.json() == {"request_id_seen": "inside-test"}  # ContextVar visible in the threadpool
    [line] = [entry for entry in log_lines() if entry.get("event") == "inside_endpoint"]
    assert line["request_id"] == "inside-test"


def test_request_id_is_none_outside_a_request():
    assert get_request_id() is None


# --- JsonFormatter ---


def _format(exc_info=None) -> dict:
    record = logging.LogRecord("app.test", logging.WARNING, __file__, 1, "hello %s", ("world",), exc_info)
    return json.loads(JsonFormatter().format(record))


def test_formatter_standard_fields():
    entry = _format()
    assert entry["message"] == "hello world"
    assert (entry["level"], entry["logger"], entry["request_id"]) == ("WARNING", "app.test", None)
    timestamp = datetime.fromisoformat(entry["timestamp"])
    assert timestamp.utcoffset().total_seconds() == 0  # UTC


def test_formatter_includes_extra_fields_and_survives_non_json_values():
    logger = logging.getLogger("tests.formatter")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    try:
        logger.warning("with extras", extra={"event": "x", "rows": 3, "path_obj": Path("a/b"), "request_id": "given"})
    finally:
        logger.removeHandler(handler)
        logger.propagate = True
    entry = json.loads(stream.getvalue())
    assert (entry["event"], entry["rows"], entry["path_obj"], entry["request_id"]) == ("x", 3, str(Path("a/b")), "given")
    assert "args" not in entry and "levelno" not in entry  # standard LogRecord internals are not dumped


def test_formatter_includes_traceback():
    try:
        raise ValueError("boom")
    except ValueError:
        entry = _format(exc_info=sys.exc_info())
    assert "ValueError: boom" in entry["traceback"]


# --- configure_logging ---


def test_configure_logging_twice_adds_one_handler():
    root = logging.getLogger()
    before = list(root.handlers)
    old_level = root.level
    access = logging.getLogger("uvicorn.access")
    old_disabled = access.disabled
    try:
        configure_logging()
        configure_logging()
        ours = [h for h in root.handlers if h.get_name() == "groundedbi-json"]
        assert len(ours) == 1
        assert isinstance(ours[0].formatter, JsonFormatter)
        assert access.disabled
    finally:
        for handler in root.handlers[:]:
            if handler not in before:
                root.removeHandler(handler)
        root.setLevel(old_level)
        access.disabled = old_disabled
