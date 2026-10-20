import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app
from scripts.seed_data import seed_database

INJECTION = "2026-01'; DROP TABLE sales; --"
SECRET = "secret-internal-detail-7731"


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("errors") / "errors.db"
    seed_database(path, seed=42, start="2026-01", months=4)
    return path


def _app_for(path):
    """A fresh app using `path` as its database, plus a test-only route that crashes."""
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(database_path=path, _env_file=None)

    @app.get("/test-boom")
    def boom() -> None:
        raise RuntimeError(SECRET)

    return app


@pytest.fixture(scope="module")
def client(db_path):
    # raise_server_exceptions=False: return the 500 response instead of re-raising in the test.
    return TestClient(_app_for(db_path), raise_server_exceptions=False)


def _assert_envelope(response, status: int, code: str) -> dict:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    error = body["error"]
    assert error["code"] == code and error["message"]
    assert error["request_id"] == response.headers["X-Request-ID"]
    return error


# --- 422 ---


def test_validation_error_envelope_with_details(client):
    response = client.get("/analytics/revenue-by-month", params={"start": "2026-13", "end": "2026-12"})
    error = _assert_envelope(response, 422, "validation_error")
    assert error["details"] == [
        {
            "location": "query",
            "field": "start",
            "message": "String should match pattern '^[0-9]{4}-(0[1-9]|1[0-2])$'",
            "type": "string_pattern_mismatch",
        }
    ]


def test_validation_error_lists_every_bad_field(client):
    response = client.get("/analytics/breakdown", params={"start": "x"})
    error = _assert_envelope(response, 422, "validation_error")
    assert {d["field"] for d in error["details"]} == {"start", "end", "dimension"}


@pytest.mark.parametrize(
    "params",
    [
        {"start": INJECTION, "end": "2026-02"},
        {"start": "2026-01", "end": "2026-02", "dimension": INJECTION},
    ],
)
def test_validation_error_does_not_echo_input(client, params):
    path = "/analytics/breakdown"
    response = client.get(path, params={"dimension": "region", **params})
    _assert_envelope(response, 422, "validation_error")
    assert "DROP TABLE" not in response.text
    assert all("input" not in detail for detail in response.json()["error"]["details"])


def test_start_after_end_uses_validation_envelope(client):
    response = client.get("/analytics/revenue-by-month", params={"start": "2026-05", "end": "2026-03"})
    error = _assert_envelope(response, 422, "validation_error")
    assert error["details"][0]["field"] == "start..end"


# --- 404 / 405 ---


def test_not_found_envelope(client):
    _assert_envelope(client.get("/nope"), 404, "not_found")


def test_method_not_allowed_envelope_keeps_allow_header(client):
    response = client.post("/analytics/meta")
    _assert_envelope(response, 405, "method_not_allowed")
    assert response.headers["Allow"] == "GET"


# --- 503 ---


def test_missing_database_envelope(tmp_path):
    missing = tmp_path / "missing.db"
    response = TestClient(_app_for(missing)).get("/analytics/meta")
    error = _assert_envelope(response, 503, "database_unavailable")
    assert "python -m scripts.seed_data" in error["message"]
    assert not missing.exists()


def _empty_file(path):
    path.write_bytes(b"")


def _sqlite_without_sales(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE something_else (x INTEGER)")
    conn.close()


@pytest.mark.parametrize("make_db", [_empty_file, _sqlite_without_sales], ids=["empty-file", "no-sales-table"])
def test_existing_but_unseeded_database_returns_503(tmp_path, make_db):
    path = tmp_path / "unseeded.db"
    make_db(path)
    response = TestClient(_app_for(path)).get("/analytics/meta")
    error = _assert_envelope(response, 503, "database_unavailable")
    assert "python -m scripts.seed_data" in error["message"]


# --- 500 ---


def test_unhandled_exception_returns_generic_500(client, log_lines):
    response = client.get("/test-boom", headers={"X-Request-ID": "boom-test"})
    error = _assert_envelope(response, 500, "internal_error")
    assert error["request_id"] == "boom-test"
    assert SECRET not in response.text and "RuntimeError" not in response.text and "Traceback" not in response.text

    lines = log_lines()
    [crash] = [entry for entry in lines if entry.get("event") == "unhandled_exception"]
    assert crash["level"] == "ERROR" and crash["request_id"] == "boom-test" and crash["path"] == "/test-boom"
    assert "Traceback" in crash["traceback"] and f"RuntimeError: {SECRET}" in crash["traceback"]
    [access] = [entry for entry in lines if entry.get("event") == "request_completed"]
    assert access["status_code"] == 500 and access["request_id"] == "boom-test"


def test_500_gets_a_generated_request_id_too(client):
    response = client.get("/test-boom")
    error = _assert_envelope(response, 500, "internal_error")
    assert len(error["request_id"]) == 32


def test_real_app_has_no_test_route():
    from app.main import app

    assert TestClient(app).get("/test-boom").status_code == 404
