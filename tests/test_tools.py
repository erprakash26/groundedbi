import json
import sqlite3

import pytest

from app.db.connection import get_connection
from app.services import analytics
from app.services.analytics import BREAKDOWN_DIMENSIONS, CHANGE_DIMENSIONS, MAX_TOP_LIMIT, Period
from app.tools import TOOLS, ToolResult, execute_tool, tool_definitions
from app.tools.results import format_cents
from scripts.seed_data import seed_database

FEB_VS_MAR = {
    "period_a_start": "2026-02",
    "period_a_end": "2026-02",
    "period_b_start": "2026-03",
    "period_b_end": "2026-03",
}
Q1 = {"start_month": "2026-01", "end_month": "2026-03"}
COVERAGE = {"first_month": "2026-01", "last_month": "2026-06"}


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    """Seeded database (seed 42, 2026-01 to 2026-06), opened read-only like the API does."""
    db_path = tmp_path_factory.mktemp("tools") / "tools.db"
    seed_database(db_path, seed=42, start="2026-01", months=6)
    connection = get_connection(db_path, read_only=True)
    yield connection
    connection.close()


def run_ok(conn, name, arguments) -> dict:
    result = execute_tool(conn, name, arguments)
    assert result.ok, result.error
    assert result.error is None
    return result.data


def assert_not_echoed(raw: str, obj) -> None:
    """raw must not appear in any string or number inside obj.

    Walks the structure instead of searching json.dumps(obj): JSON escapes "\\n" as "\\\\n",
    so a dumped string would hide an echoed value that contains a newline.
    """
    if isinstance(obj, dict):
        for key, value in obj.items():
            assert_not_echoed(raw, key)
            assert_not_echoed(raw, value)
    elif isinstance(obj, list):
        for item in obj:
            assert_not_echoed(raw, item)
    elif obj is not None:
        assert raw not in str(obj), f"raw input echoed in {obj!r}"


def run_error(conn, name, arguments, code: str) -> ToolResult:
    result = execute_tool(conn, name, arguments)
    assert not result.ok
    assert result.data is None
    assert result.error is not None and result.error.code == code
    return result


# --- money formatting ------------------------------------------------------


@pytest.mark.parametrize(
    ("cents", "expected"),
    [
        (0, "$0.00"),
        (5, "$0.05"),
        (-5, "-$0.05"),
        (199, "$1.99"),
        (-22491000, "-$224,910.00"),
        (123456789, "$1,234,567.89"),
    ],
)
def test_format_cents(cents, expected):
    assert format_cents(cents) == expected


# --- each tool returns correct data ----------------------------------------


def test_get_data_overview(conn):
    data = run_ok(conn, "get_data_overview", {})
    assert data["data_coverage"] == COVERAGE
    assert data["first_date"] == "2026-01-01" and data["last_date"] == "2026-06-30"
    assert data["products"] == analytics.list_products(conn)
    assert data["regions"] == ["East", "North", "South", "West"]
    assert data["categories"] == analytics.list_categories(conn)
    assert "Wearables & Tablets" in data["categories"]


def test_get_revenue_by_month_matches_service(conn):
    data = run_ok(conn, "get_revenue_by_month", Q1)
    expected = analytics.revenue_by_month(conn, Period("2026-01", "2026-03"))
    assert [row["month"] for row in data["rows"]] == ["2026-01", "2026-02", "2026-03"]
    assert [row["revenue_cents"] for row in data["rows"]] == [row.revenue_cents for row in expected]
    assert [row["orders"] for row in data["rows"]] == [row.orders for row in expected]
    assert data["rows"][1]["revenue_display"] == format_cents(expected[1].revenue_cents)
    assert data["period"] == Q1


def test_get_revenue_breakdown_by_region_matches_service(conn):
    data = run_ok(conn, "get_revenue_breakdown", {**Q1, "dimension": "region"})
    expected = analytics.revenue_breakdown(conn, Period("2026-01", "2026-03"), "region")
    assert data["dimension"] == "region"
    assert [(r["label"], r["revenue_cents"], r["units"]) for r in data["rows"]] == [
        (r.label, r.revenue_cents, r.units) for r in expected
    ]


def test_compare_periods_feb_vs_mar(conn):
    data = run_ok(conn, "compare_periods", FEB_VS_MAR)
    assert data["pct_change"] == "-17.2"
    assert data["change_cents"] == data["revenue_b_cents"] - data["revenue_a_cents"] == -31403200
    assert data["change_display"] == "-$314,032.00"
    assert data["revenue_a_display"] == format_cents(data["revenue_a_cents"])


def test_get_change_drivers_feb_vs_mar_by_product_region(conn):
    data = run_ok(conn, "get_change_drivers", {**FEB_VS_MAR, "dimension": "product_region", "limit": 3})
    assert len(data["rows"]) == 3
    first = data["rows"][0]
    assert first["label"] == "StudioBook 16 / West"
    assert first["share_of_total_change"] == "71.6"
    assert first["change_cents"] == -22491000
    assert first["change_display"] == "-$224,910.00"
    # The total covers ALL labels, not just the 3 returned; shares are relative to it.
    assert data["total_change_cents"] == -31403200
    assert data["driver_count"] == 32


def test_get_change_drivers_default_limit_is_5(conn):
    data = run_ok(conn, "get_change_drivers", {**FEB_VS_MAR, "dimension": "product"})
    assert len(data["rows"]) == 5


def test_get_top_products(conn):
    data = run_ok(conn, "get_top_products", {**Q1, "limit": 3})
    expected = analytics.top_products(conn, Period("2026-01", "2026-03"), 3)
    assert [r["label"] for r in data["rows"]] == [r.label for r in expected]
    revenues = [r["revenue_cents"] for r in data["rows"]]
    assert revenues == sorted(revenues, reverse=True)


# --- every result is JSON-serializable --------------------------------------

ALL_CALLS = [
    ("get_data_overview", {}),
    ("get_revenue_by_month", Q1),
    ("get_revenue_breakdown", {**Q1, "dimension": "category"}),
    ("compare_periods", FEB_VS_MAR),
    ("get_change_drivers", {**FEB_VS_MAR, "dimension": "product_region"}),
    ("get_top_products", Q1),
    ("get_revenue_by_month", {"start_month": "2030-01", "end_month": "2030-01"}),
    ("get_top_products", {**Q1, "limit": 0}),  # invalid_arguments
    ("drop_tables", {}),  # unknown_tool
]


@pytest.mark.parametrize(("name", "arguments"), ALL_CALLS)
def test_every_result_is_json_serializable(conn, name, arguments):
    # model_dump() without mode="json": data must already be plain JSON types (no Decimal).
    json.dumps(execute_tool(conn, name, arguments).model_dump())


# --- data coverage ---------------------------------------------------------


def test_fully_covered_period(conn):
    data = run_ok(conn, "get_revenue_by_month", Q1)
    assert data["data_coverage"] == COVERAGE
    assert data["period_fully_covered"] is True
    assert "uncovered_months" not in data


def test_partially_covered_period(conn):
    data = run_ok(conn, "get_revenue_by_month", {"start_month": "2025-12", "end_month": "2026-02"})
    assert data["period_fully_covered"] is False
    assert data["uncovered_months"] == ["2025-12"]
    assert [row["month"] for row in data["rows"]] == ["2026-01", "2026-02"]


def test_fully_uncovered_period_is_ok_with_empty_rows(conn):
    data = run_ok(conn, "get_revenue_by_month", {"start_month": "2030-01", "end_month": "2030-01"})
    assert data["rows"] == []
    assert data["data_coverage"] == COVERAGE
    assert data["period_fully_covered"] is False
    assert data["uncovered_months"] == ["2030-01"]


@pytest.mark.parametrize(
    ("name", "extra"),
    [
        ("get_revenue_breakdown", {"dimension": "product"}),
        ("get_top_products", {}),
    ],
)
def test_other_single_period_tools_report_coverage_when_empty(conn, name, extra):
    data = run_ok(conn, name, {"start_month": "2030-01", "end_month": "2030-02", **extra})
    assert data["rows"] == []
    assert data["data_coverage"] == COVERAGE
    assert data["uncovered_months"] == ["2030-01", "2030-02"]


def test_coverage_is_reported_per_period_for_two_period_tools(conn):
    arguments = {**FEB_VS_MAR, "period_b_start": "2026-06", "period_b_end": "2026-08"}
    data = run_ok(conn, "compare_periods", arguments)
    assert data["period_a_fully_covered"] is True
    assert "period_a_uncovered_months" not in data
    assert data["period_b_fully_covered"] is False
    assert data["period_b_uncovered_months"] == ["2026-07", "2026-08"]


def test_long_uncovered_list_is_capped_with_a_count(conn):
    data = run_ok(conn, "get_revenue_by_month", {"start_month": "2000-01", "end_month": "2099-12"})
    assert len(data["uncovered_months"]) == 24
    assert data["uncovered_months"][0] == "2000-01"
    assert data["uncovered_month_count"] == 100 * 12 - 6


def test_empty_database_has_no_coverage(empty_conn):
    data = run_ok(empty_conn, "get_revenue_by_month", Q1)
    assert data["rows"] == []
    assert data["data_coverage"] is None
    assert data["uncovered_months"] == ["2026-01", "2026-02", "2026-03"]
    overview = run_ok(empty_conn, "get_data_overview", {})
    assert overview["data_coverage"] is None and overview["products"] == []


# --- invalid_arguments ------------------------------------------------------

INJECTION = "2026-01'; DROP TABLE sales; --"


@pytest.mark.parametrize(
    ("name", "arguments", "field", "raw"),
    [
        ("get_revenue_by_month", {"start_month": "2026-13", "end_month": "2026-03"}, "start_month", "2026-13"),
        ("get_revenue_by_month", {"start_month": "2026-01\n", "end_month": "2026-03"}, "start_month", "2026-01\n"),
        ("get_revenue_by_month", {"start_month": "2026-05", "end_month": "2026-03"}, None, None),
        ("compare_periods", {**FEB_VS_MAR, "period_b_start": "2026-09"}, None, None),
        ("get_revenue_breakdown", {**Q1, "dimension": "customer"}, "dimension", "customer"),
        ("get_change_drivers", {**FEB_VS_MAR, "dimension": "product", "limit": 0}, "limit", None),
        ("get_top_products", {**Q1, "limit": 21}, "limit", None),
        ("get_top_products", {**Q1, "region": "West"}, "region", "West"),
        ("get_top_products", {"start_month": "2026-01"}, "end_month", None),
        ("get_revenue_by_month", {"start_month": 202601, "end_month": "2026-03"}, "start_month", "202601"),
        ("get_revenue_by_month", {"start_month": INJECTION, "end_month": "2026-03"}, "start_month", INJECTION),
        ("get_top_products", {**Q1, "limit": "5"}, "limit", None),  # strict: no string-to-int coercion
        ("get_top_products", {**Q1, "limit": True}, "limit", None),  # strict: a bool is not a number
    ],
)
def test_invalid_arguments(conn, name, arguments, field, raw):
    result = run_error(conn, name, arguments, "invalid_arguments")
    assert result.arguments is None
    details = result.error.details
    assert details, "field-level details expected"
    assert field in [d["field"] for d in details]
    if raw is not None:
        assert_not_echoed(raw, result.model_dump())


def test_start_after_end_message_names_the_rule(conn):
    result = run_error(
        conn, "get_revenue_by_month", {"start_month": "2026-05", "end_month": "2026-03"}, "invalid_arguments"
    )
    assert "start_month must not be after end_month" in result.error.details[0]["message"]


@pytest.mark.parametrize("arguments", [None, [], "start_month=2026-01", 42])
def test_arguments_that_are_not_an_object(conn, arguments):
    run_error(conn, "get_revenue_by_month", arguments, "invalid_arguments")


def test_extra_field_with_unsafe_name_is_not_echoed(conn):
    bad_key = "x'; DROP TABLE sales; --"
    result = run_error(conn, "get_top_products", {**Q1, bad_key: 1}, "invalid_arguments")
    assert result.error.details[0]["field"] == "<unrecognized field>"
    assert_not_echoed("DROP", result.model_dump())


def test_value_error_from_the_service_becomes_invalid_arguments(conn, monkeypatch):
    def raise_value_error(*args, **kwargs):
        raise ValueError("secret detail")

    monkeypatch.setattr(analytics, "revenue_by_month", raise_value_error)
    result = run_error(conn, "get_revenue_by_month", Q1, "invalid_arguments")
    assert_not_echoed("secret detail", result.model_dump())


# --- unknown_tool -----------------------------------------------------------


@pytest.mark.parametrize("name", ["drop_tables", "", None, ["get_data_overview"]])
def test_unknown_tool(conn, name):
    result = run_error(conn, name, {}, "unknown_tool")
    assert result.tool is None
    assert "get_data_overview" in result.error.message  # lists the valid names
    assert_not_echoed("drop_tables", result.model_dump())


# --- tool_timeout -----------------------------------------------------------


def test_timeout_aborts_the_query_and_the_connection_still_works(conn):
    arguments = {**FEB_VS_MAR, "dimension": "product_region"}
    result = execute_tool(conn, "get_change_drivers", arguments, time_budget_seconds=1e-6)
    assert not result.ok and result.data is None
    assert result.error.code == "tool_timeout"
    assert result.arguments is not None  # validation passed; only execution was stopped
    # Query directly, NOT through execute_tool: execute_tool installs a fresh handler, which
    # would hide a leftover one whose deadline has already passed.
    drivers = analytics.change_drivers(conn, Period.of_month("2026-02"), Period.of_month("2026-03"), "product_region")
    assert drivers[0].label == "StudioBook 16 / West"
    assert run_ok(conn, "get_change_drivers", arguments)["rows"][0]["label"] == "StudioBook 16 / West"


def test_other_operational_error_is_tool_failed_not_timeout(conn, monkeypatch):
    def raise_operational_error(*args, **kwargs):
        raise sqlite3.OperationalError("no such table: sales")

    monkeypatch.setattr(analytics, "revenue_by_month", raise_operational_error)
    run_error(conn, "get_revenue_by_month", Q1, "tool_failed")


# --- tool_failed ------------------------------------------------------------


def test_unexpected_exception_is_tool_failed_with_traceback_logged(conn, monkeypatch, log_lines):
    def explode(*args, **kwargs):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(analytics, "top_products", explode)
    result = run_error(conn, "get_top_products", Q1, "tool_failed")
    assert_not_echoed("secret internal detail", result.model_dump())
    assert result.error.message == "get_top_products failed because of an internal error."
    failed = [line for line in log_lines() if line.get("event") == "tool_failed"]
    assert len(failed) == 1
    assert failed[0]["level"] == "ERROR"
    assert "RuntimeError: secret internal detail" in failed[0]["traceback"]


# --- logging ----------------------------------------------------------------


def test_tool_executed_log_line(conn, log_lines):
    execute_tool(conn, "get_top_products", {**Q1, "limit": 3})
    lines = [line for line in log_lines() if line.get("event") == "tool_executed"]
    assert len(lines) == 1
    line = lines[0]
    assert line["tool"] == "get_top_products"
    assert line["ok"] is True
    assert line["error_code"] is None
    assert line["arguments"] == {**Q1, "limit": 3}
    assert isinstance(line["duration_ms"], float)


def test_tool_executed_log_line_for_invalid_call_omits_raw_arguments(conn, log_lines):
    execute_tool(conn, "get_revenue_by_month", {"start_month": INJECTION, "end_month": "2026-03"})
    line = next(line for line in log_lines() if line.get("event") == "tool_executed")
    assert line["ok"] is False
    assert line["error_code"] == "invalid_arguments"
    assert line["arguments"] is None
    assert_not_echoed("DROP", line)


def test_tool_executed_log_line_carries_the_request_id(conn, log_lines):
    from app.observability.context import request_id_var

    token = request_id_var.set("req-tools-1")
    try:
        execute_tool(conn, "get_data_overview", {})
    finally:
        request_id_var.reset(token)
    line = next(line for line in log_lines() if line.get("event") == "tool_executed")
    assert line["request_id"] == "req-tools-1"


# --- tool_definitions -------------------------------------------------------


def test_tool_definitions_shape():
    definitions = tool_definitions()
    names = [d["name"] for d in definitions]
    assert len(definitions) == 6
    assert len(set(names)) == 6
    assert set(names) == set(TOOLS)
    for definition in definitions:
        assert set(definition) == {"name", "description", "input_schema"}
        assert "Example" in definition["description"]
        schema = definition["input_schema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert isinstance(schema["required"], list)
        json.dumps(definition)


def test_tool_definitions_required_fields_and_limits():
    by_name = {d["name"]: d["input_schema"] for d in tool_definitions()}
    assert by_name["get_data_overview"]["required"] == []
    assert by_name["get_revenue_by_month"]["required"] == ["start_month", "end_month"]
    # limit has a default, so it is optional; dimension is required.
    assert "limit" not in by_name["get_change_drivers"]["required"]
    assert "dimension" in by_name["get_change_drivers"]["required"]
    limit = by_name["get_top_products"]["properties"]["limit"]
    assert (limit["minimum"], limit["maximum"], limit["default"]) == (1, MAX_TOP_LIMIT, 5)
    assert by_name["get_revenue_by_month"]["properties"]["start_month"]["pattern"] == "^[0-9]{4}-(0[1-9]|1[0-2])$"


def test_dimension_enums_match_service_constants():
    by_name = {d["name"]: d["input_schema"] for d in tool_definitions()}
    assert by_name["get_revenue_breakdown"]["properties"]["dimension"]["enum"] == list(BREAKDOWN_DIMENSIONS)
    assert by_name["get_change_drivers"]["properties"]["dimension"]["enum"] == list(CHANGE_DIMENSIONS)
