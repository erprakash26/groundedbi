import sqlite3
from typing import get_args

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.config import Settings, get_settings
from app.db.connection import get_connection
from app.main import app
from app.schemas.analytics import BreakdownDimension, ChangeDimension
from app.services import analytics
from scripts.seed_data import seed_database

FEB_VS_MAR = {"a_start": "2026-02", "a_end": "2026-02", "b_start": "2026-03", "b_end": "2026-03"}
FIRST_HALF = {"start": "2026-01", "end": "2026-06"}


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    """A seeded database for this module only; the real data/groundedbi.db is never touched."""
    path = tmp_path_factory.mktemp("api") / "api.db"
    seed_database(path, seed=42, start="2026-01", months=6)
    return path


@pytest.fixture
def make_client():
    """Return a TestClient whose requests use the given database path.

    Overriding get_settings (not get_db) means the real get_db still runs, so its
    read-only, 503 and close-on-exit behaviour is exercised by every test.
    """

    def make(path) -> TestClient:
        app.dependency_overrides[get_settings] = lambda: Settings(database_path=path, _env_file=None)
        return TestClient(app)

    yield make
    app.dependency_overrides.clear()


@pytest.fixture
def client(make_client, db_path):
    return make_client(db_path)


@pytest.fixture
def conn(db_path):
    """Direct connection to the same database, to compute expected values via the service."""
    connection = get_connection(db_path, read_only=True)
    yield connection
    connection.close()


# --- happy paths ---


def test_meta(client, conn):
    response = client.get("/analytics/meta")
    assert response.status_code == 200
    body = response.json()
    assert body["first_date"].startswith("2026-01") and body["last_date"].startswith("2026-06")
    assert body["products"] == analytics.list_products(conn)
    assert body["regions"] == analytics.list_regions(conn)


def test_revenue_by_month(client, conn):
    response = client.get("/analytics/revenue-by-month", params=FIRST_HALF)
    assert response.status_code == 200
    body = response.json()
    assert body["period"] == {"start": "2026-01", "end": "2026-06"}
    assert [row["month"] for row in body["rows"]] == [f"2026-0{m}" for m in range(1, 7)]
    expected = analytics.revenue_by_month(conn, analytics.Period("2026-01", "2026-06"))
    assert [row["revenue_cents"] for row in body["rows"]] == [m.revenue_cents for m in expected]
    assert all(isinstance(row["revenue_cents"], int) for row in body["rows"])


@pytest.mark.parametrize("dimension", get_args(BreakdownDimension))
def test_breakdown_totals_match_monthly_revenue(client, dimension):
    params = {"start": "2026-02", "end": "2026-03", "dimension": dimension}
    body = client.get("/analytics/breakdown", params=params).json()
    monthly = client.get("/analytics/revenue-by-month", params={"start": "2026-02", "end": "2026-03"}).json()
    assert body["dimension"] == dimension and body["period"] == {"start": "2026-02", "end": "2026-03"}
    assert body["rows"]
    assert sum(r["revenue_cents"] for r in body["rows"]) == sum(r["revenue_cents"] for r in monthly["rows"])


def test_compare_feb_vs_mar(client):
    response = client.get("/analytics/compare", params=FEB_VS_MAR)
    assert response.status_code == 200
    body = response.json()
    assert body["period_a"] == {"start": "2026-02", "end": "2026-02"}
    assert body["period_b"] == {"start": "2026-03", "end": "2026-03"}
    assert body["pct_change"] == "-17.2"  # Decimal serialized as a string, not a float
    assert body["change_cents"] == body["revenue_b_cents"] - body["revenue_a_cents"]


def test_drivers_feb_vs_mar_by_product_region(client):
    params = {**FEB_VS_MAR, "dimension": "product_region", "limit": 3}
    response = client.get("/analytics/drivers", params=params)
    assert response.status_code == 200
    body = response.json()
    assert body["dimension"] == "product_region"
    assert body["period_a"] == {"start": "2026-02", "end": "2026-02"}
    assert len(body["rows"]) == 3
    assert body["rows"][0]["label"] == "StudioBook 16 / West"
    assert body["rows"][0]["share_of_total_change"] == "71.6"


def test_drivers_default_limit_is_5(client):
    body = client.get("/analytics/drivers", params={**FEB_VS_MAR, "dimension": "product_region"}).json()
    assert len(body["rows"]) == 5


def test_top_products(client):
    response = client.get("/analytics/top-products", params={**FIRST_HALF, "limit": 3})
    assert response.status_code == 200
    rows = response.json()["rows"]
    assert len(rows) == 3
    assert [r["revenue_cents"] for r in rows] == sorted((r["revenue_cents"] for r in rows), reverse=True)


# --- empty results ---


@pytest.mark.parametrize(
    "path, extra",
    [
        ("/analytics/revenue-by-month", {}),
        ("/analytics/breakdown", {"dimension": "region"}),
        ("/analytics/top-products", {}),
    ],
)
def test_period_with_no_data_returns_200_and_empty_rows(client, path, extra):
    response = client.get(path, params={"start": "2030-01", "end": "2030-02", **extra})
    assert response.status_code == 200
    body = response.json()
    assert body["rows"] == []
    assert body["period"] == {"start": "2030-01", "end": "2030-02"}


def test_compare_with_empty_baseline_gives_null_pct(client):
    params = {"a_start": "2030-01", "a_end": "2030-01", "b_start": "2026-03", "b_end": "2026-03"}
    body = client.get("/analytics/compare", params=params).json()
    assert body["revenue_a_cents"] == 0 and body["pct_change"] is None


# --- validation: 422 ---

INJECTION = "2026-01'; DROP TABLE sales; --"


@pytest.mark.parametrize(
    "path, params",
    [
        ("/analytics/revenue-by-month", {"start": "2026-13", "end": "2026-12"}),
        ("/analytics/revenue-by-month", {"start": "2026-1", "end": "2026-02"}),
        ("/analytics/revenue-by-month", {"start": "2026-01"}),  # missing end
        ("/analytics/revenue-by-month", {"start": INJECTION, "end": "2026-02"}),
        ("/analytics/revenue-by-month", {"start": "2026-01\n", "end": "2026-02"}),  # trailing newline
        ("/analytics/revenue-by-month", {"start": "٢٠٢٦-01", "end": "2026-02"}),  # non-ASCII digits
        ("/analytics/breakdown", {**FIRST_HALF, "dimension": "store"}),
        ("/analytics/breakdown", {**FIRST_HALF, "dimension": "product_region"}),  # change-only dimension
        ("/analytics/breakdown", {**FIRST_HALF, "dimension": "region; DROP TABLE sales"}),
        ("/analytics/drivers", {**FEB_VS_MAR, "dimension": "product", "limit": 0}),
        ("/analytics/drivers", {**FEB_VS_MAR, "dimension": "product", "limit": 21}),
        ("/analytics/drivers", {**FEB_VS_MAR, "dimension": "product", "limit": "five"}),
        ("/analytics/top-products", {**FIRST_HALF, "limit": 0}),
        ("/analytics/top-products", {**FIRST_HALF, "limit": 21}),
    ],
)
def test_invalid_query_parameters_return_422(client, path, params):
    assert client.get(path, params=params).status_code == 422


@pytest.mark.parametrize(
    "path, params, names",
    [
        ("/analytics/revenue-by-month", {"start": "2026-05", "end": "2026-03"}, "start..end"),
        ("/analytics/compare", {**FEB_VS_MAR, "b_start": "2026-04", "b_end": "2026-03"}, "b_start..b_end"),
        ("/analytics/drivers", {**FEB_VS_MAR, "a_start": "2026-03", "dimension": "region"}, "a_start..a_end"),
    ],
)
def test_start_after_end_returns_422_naming_the_period(client, path, params, names):
    response = client.get(path, params=params)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert names in error["message"] and "after" in error["message"]


def test_injection_attempts_leave_data_intact(client, conn):
    before = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
    client.get("/analytics/revenue-by-month", params={"start": INJECTION, "end": "2026-02"})
    client.get("/analytics/breakdown", params={**FIRST_HALF, "dimension": "region; DROP TABLE sales"})
    assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == before


def test_dimension_literals_match_service_dimensions():
    # The API's allowed values and the SQL fragments in queries.py must never drift apart.
    assert get_args(BreakdownDimension) == analytics.BREAKDOWN_DIMENSIONS
    assert get_args(ChangeDimension) == analytics.CHANGE_DIMENSIONS


# --- database access ---


def test_missing_database_returns_503_with_seed_hint_and_creates_nothing(make_client, tmp_path):
    missing = tmp_path / "missing.db"
    response = make_client(missing).get("/analytics/meta")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"
    assert "python -m scripts.seed_data" in response.json()["error"]["message"]
    assert not missing.exists()


def test_get_db_connection_is_read_only_and_closed_afterwards(db_path):
    dependency = get_db(Settings(database_path=db_path, _env_file=None))
    conn = next(dependency)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO regions (name) VALUES ('Nowhere')")
    dependency.close()  # what FastAPI does when the request ends: runs the finally block
    with pytest.raises(sqlite3.ProgrammingError):  # using a closed connection
        conn.execute("SELECT 1")


# --- routing ---


def test_post_to_analytics_endpoint_returns_405(client):
    assert client.post("/analytics/meta").status_code == 405


def test_health_still_ok(client):
    assert client.get("/health").json() == {"status": "ok"}
