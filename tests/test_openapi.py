"""The OpenAPI document describes the errors the API really returns."""

import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.schemas.errors import ErrorResponse
from scripts.seed_data import seed_database

ERROR_REF = "#/components/schemas/ErrorResponse"
SPEC = app.openapi()
OPERATIONS = [
    (method.upper(), path, operation)
    for path, methods in SPEC["paths"].items()
    for method, operation in methods.items()
]
DB_PATHS = {path for path in SPEC["paths"] if path.startswith("/analytics") or path == "/ask"}


@pytest.fixture(scope="module")
def seeded_client(tmp_path_factory):
    """Real get_db on a seeded test database, so checks made after get_db (period order) run too."""
    path = tmp_path_factory.mktemp("openapi") / "openapi.db"
    seed_database(path, seed=42, start="2026-01", months=6)
    app.dependency_overrides[get_settings] = lambda: Settings(database_path=path, _env_file=None)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _ref(operation: dict, status: str) -> str | None:
    return operation["responses"][status]["content"]["application/json"]["schema"].get("$ref")


def _ids():
    return [f"{method} {path}" for method, path, _ in OPERATIONS]


def test_fastapi_default_validation_models_are_not_documented():
    text = json.dumps(SPEC)
    assert "HTTPValidationError" not in text
    assert "ValidationError" not in SPEC["components"]["schemas"]


@pytest.mark.parametrize(("method", "path", "operation"), OPERATIONS, ids=_ids())
def test_every_documented_422_uses_our_error_model(method, path, operation):
    if "422" in operation["responses"]:
        assert _ref(operation, "422") == ERROR_REF


@pytest.mark.parametrize(("method", "path", "operation"), OPERATIONS, ids=_ids())
def test_every_route_with_input_documents_422(method, path, operation):
    takes_input = bool(operation.get("parameters")) or "requestBody" in operation
    assert ("422" in operation["responses"]) == takes_input  # and routes without input don't claim one


@pytest.mark.parametrize(("method", "path", "operation"), OPERATIONS, ids=_ids())
def test_every_route_documents_500_with_our_error_model(method, path, operation):
    assert _ref(operation, "500") == ERROR_REF


@pytest.mark.parametrize(("method", "path", "operation"), OPERATIONS, ids=_ids())
def test_database_routes_document_503_and_others_do_not(method, path, operation):
    if path in DB_PATHS:
        assert _ref(operation, "503") == ERROR_REF
    else:
        assert "503" not in operation["responses"]


def test_documented_routes_include_ask_and_every_analytics_endpoint():
    assert {path for _, path, _ in OPERATIONS} >= {
        "/ask", "/health", "/analytics/meta", "/analytics/revenue-by-month", "/analytics/breakdown",
        "/analytics/compare", "/analytics/drivers", "/analytics/top-products",
    }  # fmt: skip


def test_error_model_has_no_input_field():
    detail_fields = SPEC["components"]["schemas"]["ErrorDetail"]["properties"]
    assert set(detail_fields) == {"location", "field", "message", "type"}


@pytest.mark.parametrize(
    ("method", "url", "kwargs"),
    [
        ("post", "/ask", {"json": {"question": ""}}),
        ("post", "/ask", {"json": {"question": "x", "'; DROP TABLE sales;--": 1}}),
        ("get", "/analytics/revenue-by-month", {"params": {"start": "2026-13", "end": "2026-03"}}),
        ("get", "/analytics/revenue-by-month", {"params": {"start": "2026-05", "end": "2026-03"}}),  # period order
    ],
)
def test_real_422_bodies_match_the_documented_model(seeded_client, method, url, kwargs):
    response = getattr(seeded_client, method)(url, **kwargs)
    assert response.status_code == 422
    ErrorResponse.model_validate(response.json())  # raises if the real body drifts from the docs


def test_real_503_body_matches_the_documented_model(tmp_path):
    previous = dict(app.dependency_overrides)  # don't disturb the module's seeded_client override
    app.dependency_overrides[get_settings] = lambda: Settings(database_path=tmp_path / "missing.db", _env_file=None)
    try:
        response = TestClient(app).get("/analytics/meta")
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
    assert response.status_code == 503
    ErrorResponse.model_validate(response.json())
