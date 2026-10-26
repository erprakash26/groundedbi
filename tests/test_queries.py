import pytest

from app.db.queries import (
    BreakdownRow,
    DateRange,
    MonthRevenue,
    Period,
    data_date_range,
    list_categories,
    list_products,
    list_regions,
    period_totals,
    revenue_breakdown,
    revenue_by_month,
    revenue_changes,
    top_products,
)

FULL = Period("2026-01", "2026-06")
MONTH_2 = Period.of_month("2026-02")
MONTH_3 = Period.of_month("2026-03")
NO_DATA = Period.of_month("2030-01")


def table_revenue(conn):
    return conn.execute("SELECT SUM(revenue_cents) FROM sales").fetchone()[0]


def sales_count(conn):
    return conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]


# --- Period ----------------------------------------------------------------


def test_period_date_bounds_are_half_open():
    assert Period("2026-01", "2026-02").date_bounds() == ("2026-01-01", "2026-03-01")
    assert Period.of_month("2025-12").date_bounds() == ("2025-12-01", "2026-01-01")


@pytest.mark.parametrize(
    "start, end",
    [
        ("2026-13", "2026-13"),
        ("2026-00", "2026-01"),
        ("2026-1", "2026-02"),
        ("26-01", "26-02"),
        ("2026-01-01", "2026-02"),
        (None, "2026-02"),
        ("2026-03", "2026-02"),  # start after end
        ("2026-01\n", "2026-02"),  # trailing newline
        ("٢٠٢٦-01", "2026-02"),  # Arabic-Indic digits for 2026
    ],
)
def test_invalid_period_raises(start, end):
    with pytest.raises(ValueError):
        Period(start, end)


# --- Metadata --------------------------------------------------------------


def test_data_date_range(seeded_conn):
    assert data_date_range(seeded_conn) == DateRange("2026-01-01", "2026-06-30")


def test_data_date_range_empty_database(empty_conn):
    assert data_date_range(empty_conn) is None


def test_list_products_and_regions_sorted(seeded_conn):
    products = list_products(seeded_conn)
    assert len(products) == 8
    assert products == sorted(products)
    assert list_regions(seeded_conn) == ["East", "North", "South", "West"]


def test_list_categories_distinct_and_sorted(seeded_conn):
    categories = list_categories(seeded_conn)
    assert "Wearables & Tablets" in categories
    assert categories == sorted(set(categories))


def test_list_categories_empty_database(empty_conn):
    assert list_categories(empty_conn) == []


# --- revenue_by_month and period_totals ------------------------------------


def test_revenue_by_month_full_range_sums_to_table_total(seeded_conn):
    rows = revenue_by_month(seeded_conn, FULL)
    assert [row.month for row in rows] == ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]
    assert sum(row.revenue_cents for row in rows) == table_revenue(seeded_conn)
    assert sum(row.orders for row in rows) == sales_count(seeded_conn)


def test_period_totals_full_range_matches_table(seeded_conn):
    totals = period_totals(seeded_conn, FULL)
    assert totals.revenue_cents == table_revenue(seeded_conn)
    assert totals.orders == sales_count(seeded_conn)


def test_month_boundary_days_land_in_the_right_month(tiny_conn):
    # 2026-01-31 belongs to January only; 2026-02-01 to February only.
    assert revenue_by_month(tiny_conn, Period("2026-01", "2026-02")) == [
        MonthRevenue("2026-01", 2500, 3, 2),
        MonthRevenue("2026-02", 1500, 3, 1),
    ]


def test_empty_period_returns_empty_lists(seeded_conn):
    assert revenue_by_month(seeded_conn, NO_DATA) == []
    for dimension in ("product", "region", "category"):
        assert revenue_breakdown(seeded_conn, NO_DATA, dimension) == []
    assert top_products(seeded_conn, NO_DATA, 5) == []


# --- revenue_breakdown -----------------------------------------------------


@pytest.mark.parametrize("dimension", ["product", "region", "category"])
def test_breakdown_sums_to_period_total_and_is_sorted(seeded_conn, dimension):
    rows = revenue_breakdown(seeded_conn, MONTH_3, dimension)
    assert sum(row.revenue_cents for row in rows) == period_totals(seeded_conn, MONTH_3).revenue_cents
    revenues = [row.revenue_cents for row in rows]
    assert revenues == sorted(revenues, reverse=True)


def test_breakdown_by_region_labels(seeded_conn):
    labels = {row.label for row in revenue_breakdown(seeded_conn, MONTH_3, "region")}
    assert labels == {"North", "South", "East", "West"}


# --- revenue_changes -------------------------------------------------------


def test_revenue_changes_sum_exactly_to_total_change(seeded_conn):
    rows = revenue_changes(seeded_conn, MONTH_2, MONTH_3, "product_region")
    total_change = period_totals(seeded_conn, MONTH_3).revenue_cents - period_totals(seeded_conn, MONTH_2).revenue_cents
    assert len(rows) == 32  # 8 products x 4 regions
    assert sum(row.change_cents for row in rows) == total_change
    assert rows[0].label == "StudioBook 16 / West"
    abs_changes = [abs(row.change_cents) for row in rows]
    assert abs_changes == sorted(abs_changes, reverse=True)


def test_revenue_changes_include_label_sold_in_one_period_only(tiny_conn):
    rows = revenue_changes(tiny_conn, Period.of_month("2026-01"), Period.of_month("2026-02"), "product")
    assert [(r.label, r.revenue_a_cents, r.revenue_b_cents, r.change_cents) for r in rows] == [
        ("Alpha", 2000, 0, -2000),
        ("Beta", 500, 1500, 1000),
    ]


# --- top_products ----------------------------------------------------------


def test_top_products_sorted_and_limited(seeded_conn):
    rows = top_products(seeded_conn, FULL, 3)
    assert len(rows) == 3
    revenues = [row.revenue_cents for row in rows]
    assert revenues == sorted(revenues, reverse=True)
    assert rows == revenue_breakdown(seeded_conn, FULL, "product")[:3]
    assert all(isinstance(row, BreakdownRow) for row in rows)


def test_top_products_limit_bounds_are_inclusive(seeded_conn):
    assert len(top_products(seeded_conn, FULL, 1)) == 1
    assert len(top_products(seeded_conn, FULL, 20)) == 8  # only 8 products exist


@pytest.mark.parametrize("limit", [0, 21, -1, True, 2.5, "3", None])
def test_top_products_invalid_limit_raises(seeded_conn, limit):
    with pytest.raises(ValueError):
        top_products(seeded_conn, FULL, limit)


# --- Validation of dimensions ----------------------------------------------


@pytest.mark.parametrize("dimension", ["store", "PRODUCT", "", None, ["product"], "product_region"])
def test_breakdown_unknown_dimension_raises(seeded_conn, dimension):
    # product_region is valid for revenue_changes but not for revenue_breakdown.
    with pytest.raises(ValueError):
        revenue_breakdown(seeded_conn, MONTH_3, dimension)


@pytest.mark.parametrize("dimension", ["store", "", None])
def test_revenue_changes_unknown_dimension_raises(seeded_conn, dimension):
    with pytest.raises(ValueError):
        revenue_changes(seeded_conn, MONTH_2, MONTH_3, dimension)


# --- SQL injection ---------------------------------------------------------


def test_injection_in_month_is_rejected(seeded_conn):
    before = sales_count(seeded_conn)
    with pytest.raises(ValueError):
        Period("2026-03'; DROP TABLE sales;--", "2026-03")
    with pytest.raises(ValueError):
        Period.of_month("2026-03'; DROP TABLE sales;--")
    assert sales_count(seeded_conn) == before > 0


def test_injection_in_dimension_is_rejected(seeded_conn):
    before = sales_count(seeded_conn)
    with pytest.raises(ValueError):
        revenue_breakdown(seeded_conn, MONTH_3, "region; DROP TABLE sales")
    with pytest.raises(ValueError):
        revenue_changes(seeded_conn, MONTH_2, MONTH_3, "region; DROP TABLE sales")
    assert sales_count(seeded_conn) == before > 0
