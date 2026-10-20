from decimal import Decimal

import pytest

from app.services.analytics import (
    MAX_TOP_LIMIT,
    BreakdownRow,
    ChangeDriver,
    DateRange,
    MonthRevenue,
    Period,
    change_drivers,
    compare_periods,
    data_date_range,
    list_products,
    list_regions,
    revenue_breakdown,
    revenue_by_month,
    top_products,
)

MONTH_2 = Period.of_month("2026-02")
MONTH_3 = Period.of_month("2026-03")
JAN = Period.of_month("2026-01")
FEB = Period.of_month("2026-02")


def test_month3_vs_month2_drops_15_to_20_percent(seeded_conn):
    result = compare_periods(seeded_conn, MONTH_2, MONTH_3)
    assert result.change_cents == result.revenue_b_cents - result.revenue_a_cents
    assert isinstance(result.pct_change, Decimal)
    assert Decimal("-20") <= result.pct_change <= Decimal("-15")


def test_compare_periods_with_no_baseline_gives_none(tiny_conn):
    result = compare_periods(tiny_conn, Period.of_month("2025-12"), JAN)
    assert (result.revenue_a_cents, result.revenue_b_cents, result.change_cents) == (0, 2500, 2500)
    assert result.pct_change is None


def test_studiobook_west_is_top_driver_with_at_least_60_percent_share(seeded_conn):
    drivers = change_drivers(seeded_conn, MONTH_2, MONTH_3, "product_region")
    top = drivers[0]
    assert top.label == "StudioBook 16 / West"
    assert top.share_of_total_change >= Decimal("60")
    total_change = compare_periods(seeded_conn, MONTH_2, MONTH_3).change_cents
    assert sum(d.change_cents for d in drivers) == total_change


def test_label_sold_in_one_period_only(tiny_conn):
    # Total change is -1,000 cents: Alpha -2,000 (gone in Feb) and Beta +1,000.
    drivers = change_drivers(tiny_conn, JAN, FEB, "product")
    assert drivers == [
        ChangeDriver("Alpha", 2000, 0, -2000, Decimal("200.0")),
        ChangeDriver("Beta", 500, 1500, 1000, Decimal("-100.0")),
    ]


def test_no_total_change_gives_none_shares(tiny_conn):
    drivers = change_drivers(tiny_conn, JAN, JAN, "product")
    assert drivers and all(d.change_cents == 0 and d.share_of_total_change is None for d in drivers)


def test_change_drivers_unknown_dimension_raises(seeded_conn):
    with pytest.raises(ValueError):
        change_drivers(seeded_conn, MONTH_2, MONTH_3, "region; DROP TABLE sales")


# --- thin wrappers over queries.py ---

FIRST_HALF = Period("2026-01", "2026-06")


def test_data_date_range_covers_seeded_months(seeded_conn):
    date_range = data_date_range(seeded_conn)
    assert isinstance(date_range, DateRange)
    assert date_range.first_date.startswith("2026-01") and date_range.last_date.startswith("2026-06")


def test_data_date_range_empty_database_gives_none(empty_conn):
    assert data_date_range(empty_conn) is None


def test_list_products_and_regions_are_sorted_and_non_empty(seeded_conn):
    products, regions = list_products(seeded_conn), list_regions(seeded_conn)
    assert "StudioBook 16" in products and "West" in regions
    assert products == sorted(products) and regions == sorted(regions)


def test_list_products_and_regions_empty_database(empty_conn):
    assert list_products(empty_conn) == [] and list_regions(empty_conn) == []


def test_revenue_by_month_known_values(tiny_conn):
    assert revenue_by_month(tiny_conn, Period("2026-01", "2026-02")) == [
        MonthRevenue("2026-01", 2500, 3, 2),
        MonthRevenue("2026-02", 1500, 3, 1),
    ]


def test_revenue_by_month_empty_period(seeded_conn):
    assert revenue_by_month(seeded_conn, Period("2030-01", "2030-02")) == []


def test_revenue_breakdown_known_values(tiny_conn):
    assert revenue_breakdown(tiny_conn, JAN, "product") == [
        BreakdownRow("Alpha", 2000, 2),
        BreakdownRow("Beta", 500, 1),
    ]


def test_revenue_breakdown_unknown_dimension_raises(seeded_conn):
    with pytest.raises(ValueError):
        revenue_breakdown(seeded_conn, JAN, "product_region")


def test_top_products_respects_limit_and_order(seeded_conn):
    rows = top_products(seeded_conn, FIRST_HALF, 3)
    assert len(rows) == 3
    assert [r.revenue_cents for r in rows] == sorted((r.revenue_cents for r in rows), reverse=True)


@pytest.mark.parametrize("limit", [0, MAX_TOP_LIMIT + 1])
def test_top_products_invalid_limit_raises(seeded_conn, limit):
    with pytest.raises(ValueError):
        top_products(seeded_conn, FIRST_HALF, limit)


def test_change_drivers_limit_keeps_shares_of_full_total(seeded_conn):
    all_drivers = change_drivers(seeded_conn, MONTH_2, MONTH_3, "product_region")
    top_two = change_drivers(seeded_conn, MONTH_2, MONTH_3, "product_region", limit=2)
    # Truncation happens after shares are computed, so the shares are unchanged.
    assert top_two == all_drivers[:2]


@pytest.mark.parametrize("limit", [0, -1, True, 2.5])
def test_change_drivers_invalid_limit_raises(seeded_conn, limit):
    with pytest.raises(ValueError):
        change_drivers(seeded_conn, MONTH_2, MONTH_3, "product", limit=limit)
