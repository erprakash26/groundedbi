"""Public-deployment hardening: Cache-Control, the request body limit and nosniff."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.api.http_policy import (
    CACHEABLE,
    MAX_BODY_BYTES,
    NOT_CACHEABLE,
    BodySizeLimitMiddleware,
    cache_control_for,
)
from app.config import Settings, get_settings
from app.main import create_app
from scripts.seed_data import seed_database

FIRST_QUARTER = {"start": "2026-01", "end": "2026-03"}
TWO_PERIODS = {"a_start": "2026-02", "a_end": "2026-02", "b_start": "2026-03", "b_end": "2026-03"}
ANALYTICS_GETS = [
    ("/analytics/meta", {}),
    ("/analytics/revenue-by-month", FIRST_QUARTER),
    ("/analytics/breakdown", {**FIRST_QUARTER, "dimension": "region"}),
    ("/analytics/top-products", {**FIRST_QUARTER, "limit": 3}),
    ("/analytics/compare", TWO_PERIODS),
    ("/analytics/drivers", {**TWO_PERIODS, "dimension": "product"}),
]


def _client(db_path) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(database_path=db_path, _env_file=None)

    @app.get("/analytics/test-boom")  # test-only route: an unhandled exception, i.e. the bare 500 path
    def boom() -> None:
        raise RuntimeError("secret detail")

    # raise_server_exceptions=False: get the 500 response instead of the re-raised exception.
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    path = tmp_path_factory.mktemp("hardening") / "test.db"
    seed_database(path, seed=42, start="2026-01", months=6)
    return _client(path)


@pytest.fixture
def client_without_db(tmp_path):
    return _client(tmp_path / "missing.db")


def _ask_body(size: int) -> bytes:
    """A JSON body of exactly `size` bytes (padding inside the question string)."""
    empty = json.dumps({"question": ""}).encode()
    return json.dumps({"question": "x" * (size - len(empty))}).encode()


# --- Cache-Control ---


@pytest.mark.parametrize(("url", "params"), ANALYTICS_GETS, ids=[url for url, _ in ANALYTICS_GETS])
def test_successful_analytics_gets_are_cacheable(client, url, params):
    response = client.get(url, params=params)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=300, s-maxage=3600"


def test_ask_examples_are_cacheable(client):
    response = client.get("/ask/examples")
    assert response.status_code == 200
    assert response.headers["cache-control"] == CACHEABLE


def test_post_ask_is_never_cached(client):
    response = client.post("/ask", json={"question": "What was revenue in March 2026?"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    ("method", "url", "params", "status"),
    [
        ("GET", "/analytics/breakdown", {**FIRST_QUARTER, "dimension": "planet"}, 422),
        ("GET", "/analytics/revenue-by-month", {"start": "2026-05", "end": "2026-03"}, 422),
        ("GET", "/analytics/nope", {}, 404),
        ("POST", "/analytics/meta", {}, 405),
        ("GET", "/no-such-page", {}, 404),  # outside the API: still an error envelope, still no-store
        ("GET", "/analytics/test-boom", {}, 500),
    ],
)
def test_errors_are_never_cached(client, method, url, params, status):
    response = client.request(method, url, params=params)
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"


def test_unhandled_500_keeps_the_generic_message(client):
    body = client.get("/analytics/test-boom").json()
    assert body["error"]["code"] == "internal_error" and "secret" not in json.dumps(body)


def test_503_is_never_cached(client_without_db):
    response = client_without_db.get("/analytics/meta")
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"


def test_pages_static_and_health_get_no_cache_policy_from_us(client):
    for url in ("/", "/static/styles.css", "/health"):
        assert "cache-control" not in client.get(url).headers


@pytest.mark.parametrize(
    ("method", "path", "status", "expected"),
    [
        ("GET", "/analytics/meta", 200, CACHEABLE),
        ("GET", "/ask/examples", 200, CACHEABLE),
        ("HEAD", "/analytics/meta", 200, NOT_CACHEABLE),
        ("POST", "/ask", 200, NOT_CACHEABLE),
        ("GET", "/ask", 405, NOT_CACHEABLE),
        ("GET", "/analytics/meta", 304, NOT_CACHEABLE),
        ("GET", "/analyticsx", 200, None),  # prefix match is on "/analytics/", not "/analytics"
        ("GET", "/askme", 200, None),
        ("GET", "/explore", 200, None),
    ],
)
def test_cache_control_rule(method, path, status, expected):
    assert cache_control_for(method, path, status) == expected


# --- request body limit ---


def test_normal_ask_still_works(client):
    response = client.post("/ask", json={"question": "Why did revenue drop in March compared with February?"})
    assert response.status_code == 200
    assert response.json()["status"] == "answered"


def test_body_exactly_at_the_limit_passes_the_limit(client):
    response = client.post("/ask", content=_ask_body(MAX_BODY_BYTES), headers={"content-type": "application/json"})
    assert response.status_code == 422  # reached AskRequest (question too long), not refused as too large


def test_declared_large_body_is_413_with_our_envelope(client):
    body = _ask_body(MAX_BODY_BYTES + 1)
    response = client.post("/ask", content=body, headers={"content-type": "application/json"})
    assert response.request.headers["content-length"] == str(MAX_BODY_BYTES + 1)
    assert response.status_code == 413
    error = response.json()["error"]
    assert error["code"] == "payload_too_large"
    assert error["message"] == "The request body is larger than 16 KB."
    assert error["request_id"] == response.headers["x-request-id"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_undeclared_large_body_is_413(client):
    def chunks():  # a generator makes httpx send Transfer-Encoding: chunked, with no Content-Length
        for _ in range(20):
            yield b"x" * 1024

    response = client.post("/ask", content=chunks(), headers={"content-type": "application/json"})
    assert "content-length" not in response.request.headers
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_undeclared_small_body_reaches_the_route(client):
    def chunks():
        yield b'{"question": "What was revenue'
        yield b' in March 2026?"}'

    response = client.post("/ask", content=chunks(), headers={"content-type": "application/json"})
    assert response.status_code == 200
    assert response.json()["status"] == "answered"


def test_a_false_small_content_length_does_not_bypass_the_limit():
    # The declared length is only a claim: the bytes actually received are counted too.
    reached = []

    async def app(scope, receive, send):
        reached.append(True)

    messages = [{"type": "http.request", "body": b"x" * 10_000, "more_body": True},
                {"type": "http.request", "body": b"x" * 10_000, "more_body": False}]
    sent = []

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/ask", "headers": [(b"content-length", b"10")],
             "query_string": b"", "state": {}}
    asyncio.run(BodySizeLimitMiddleware(app)(scope, receive, send))
    assert not reached
    assert sent[0]["status"] == 413


def test_413_is_documented_on_ask():
    spec = create_app().openapi()
    assert "413" in spec["paths"]["/ask"]["post"]["responses"]


# --- nosniff ---


@pytest.mark.parametrize(
    ("method", "url", "status"),
    [
        ("GET", "/analytics/meta", 200),
        ("GET", "/health", 200),
        ("GET", "/static/app.js", 200),
        ("GET", "/analytics/nope", 404),
        ("GET", "/analytics/test-boom", 500),
    ],
)
def test_every_response_has_nosniff(client, method, url, status):
    response = client.request(method, url)
    assert response.status_code == status
    assert response.headers["x-content-type-options"] == "nosniff"


def test_json_response_has_nosniff_but_not_the_page_policy(client):
    response = client.get("/analytics/meta")
    assert response.headers["content-type"] == "application/json"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" not in response.headers


def test_pages_keep_one_nosniff_header_and_the_csp(client):
    response = client.get("/")
    assert response.headers.get_list("x-content-type-options") == ["nosniff"]  # not duplicated
    assert "content-security-policy" in response.headers
