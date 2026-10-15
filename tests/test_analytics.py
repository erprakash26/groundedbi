from decimal import Decimal

import pytest

from app.services.analytics import ChangeDriver, Period, change_drivers, compare_periods

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
