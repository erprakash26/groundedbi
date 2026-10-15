import pytest

from app.db.connection import get_connection
from scripts.seed_data import (
    BASE_MONTHLY_UNITS,
    REGIONS,
    generate_sales,
    monthly_unit_targets,
    seed_database,
)

# (start, months, first expected date, last expected date)
RANGES = [
    ("2026-01", 6, "2026-01-01", "2026-06-30"),
    ("2025-07", 5, "2025-07-01", "2025-11-30"),
]


@pytest.fixture(params=RANGES, ids=[f"{r[0]}x{r[1]}" for r in RANGES])
def seeded(tmp_path, request):
    start, months, first_date, last_date = request.param
    db_path = tmp_path / "test.db"
    seed_database(db_path, seed=42, start=start, months=months)
    conn = get_connection(db_path)
    yield conn, months, first_date, last_date
    conn.close()


def monthly_revenue(conn, category=None):
    """Revenue per month as a list of (month, revenue_cents), optionally for one category."""
    sql = (
        "SELECT substr(s.sale_date, 1, 7) AS month, SUM(s.revenue_cents) AS revenue "
        "FROM sales s JOIN products p ON p.product_id = s.product_id "
        "WHERE (? IS NULL OR p.category = ?) "
        "GROUP BY month ORDER BY month"
    )
    return [(r["month"], r["revenue"]) for r in conn.execute(sql, (category, category))]


def cell_revenue(conn, product, region, month):
    row = conn.execute(
        "SELECT COALESCE(SUM(s.revenue_cents), 0) AS revenue "
        "FROM sales s "
        "JOIN products p ON p.product_id = s.product_id "
        "JOIN regions r ON r.region_id = s.region_id "
        "WHERE p.name = ? AND r.name = ? AND substr(s.sale_date, 1, 7) = ?",
        (product, region, month),
    ).fetchone()
    return row["revenue"]


def total_revenue(db_path):
    conn = get_connection(db_path)
    try:
        return conn.execute("SELECT SUM(revenue_cents) AS total FROM sales").fetchone()["total"]
    finally:
        conn.close()


# --- Integrity -------------------------------------------------------------


@pytest.mark.parametrize("table", ["products", "regions", "sales"])
def test_every_table_has_rows(seeded, table):
    conn = seeded[0]
    # Table names cannot be ? parameters; the name comes from the fixed whitelist above.
    queries = {
        "products": "SELECT COUNT(*) FROM products",
        "regions": "SELECT COUNT(*) FROM regions",
        "sales": "SELECT COUNT(*) FROM sales",
    }
    assert conn.execute(queries[table]).fetchone()[0] > 0


def test_no_foreign_key_violations(seeded):
    conn = seeded[0]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_all_quantities_positive(seeded):
    conn = seeded[0]
    assert conn.execute("SELECT COUNT(*) FROM sales WHERE quantity <= 0").fetchone()[0] == 0


def test_revenue_equals_quantity_times_price(seeded):
    conn = seeded[0]
    mismatches = conn.execute(
        "SELECT COUNT(*) FROM sales WHERE revenue_cents != quantity * unit_price_cents"
    ).fetchone()[0]
    assert mismatches == 0


def test_dates_stay_inside_range(seeded):
    conn, months, first_date, last_date = seeded
    row = conn.execute("SELECT MIN(sale_date) AS first, MAX(sale_date) AS last FROM sales").fetchone()
    assert row["first"] >= first_date
    assert row["last"] <= last_date
    assert len(monthly_revenue(conn)) == months


# --- Determinism -----------------------------------------------------------


def test_same_seed_gives_identical_data(tmp_path):
    assert generate_sales(42, "2026-01", 6) == generate_sales(42, "2026-01", 6)
    seed_database(tmp_path / "a.db", seed=42)
    seed_database(tmp_path / "b.db", seed=42)
    assert total_revenue(tmp_path / "a.db") == total_revenue(tmp_path / "b.db")


def test_different_seed_gives_different_total(tmp_path):
    seed_database(tmp_path / "a.db", seed=42)
    seed_database(tmp_path / "b.db", seed=7)
    assert total_revenue(tmp_path / "a.db") != total_revenue(tmp_path / "b.db")


# --- Planted patterns ------------------------------------------------------


def test_pattern_a_month3_drops_15_to_20_percent(seeded):
    revenue = [r for _, r in monthly_revenue(seeded[0])]
    decline = 1 - revenue[2] / revenue[1]
    assert 0.15 <= decline <= 0.20


def test_pattern_b_studiobook_west_drives_most_of_decline(seeded):
    conn = seeded[0]
    months = monthly_revenue(conn)
    (m2, total_m2), (m3, total_m3) = months[1], months[2]
    total_decline = total_m2 - total_m3
    studiobook_decline = cell_revenue(conn, "StudioBook 16", "West", m2) - cell_revenue(
        conn, "StudioBook 16", "West", m3
    )
    assert studiobook_decline / total_decline >= 0.60


def test_pattern_b_studiobook_west_near_zero_from_day_8(seeded):
    conn = seeded[0]
    m3 = monthly_revenue(conn)[2][0]
    units = conn.execute(
        "SELECT COALESCE(SUM(s.quantity), 0) "
        "FROM sales s "
        "JOIN products p ON p.product_id = s.product_id "
        "JOIN regions r ON r.region_id = s.region_id "
        "WHERE p.name = ? AND r.name = ? AND substr(s.sale_date, 1, 7) = ? "
        "AND CAST(substr(s.sale_date, 9, 2) AS INTEGER) >= ?",
        ("StudioBook 16", "West", m3, 8),
    ).fetchone()[0]
    assert units <= 2


def test_pattern_b_studiobook_west_restocks_in_month_4(seeded):
    conn = seeded[0]
    months = monthly_revenue(conn)
    m2, m4 = months[1][0], months[3][0]
    month2 = cell_revenue(conn, "StudioBook 16", "West", m2)
    month4 = cell_revenue(conn, "StudioBook 16", "West", m4)
    # Back to normal volume: the stockout is a one-month event.
    assert abs(month4 / month2 - 1) <= 0.10

    # And it sells throughout month 4, not only before day 8.
    units = conn.execute(
        "SELECT "
        "  COALESCE(SUM(s.quantity), 0) AS total, "
        "  COALESCE(SUM(CASE WHEN CAST(substr(s.sale_date, 9, 2) AS INTEGER) >= ? THEN s.quantity END), 0) AS late "
        "FROM sales s "
        "JOIN products p ON p.product_id = s.product_id "
        "JOIN regions r ON r.region_id = s.region_id "
        "WHERE p.name = ? AND r.name = ? AND substr(s.sale_date, 1, 7) = ?",
        (8, "StudioBook 16", "West", m4),
    ).fetchone()
    assert units["late"] >= units["total"] / 2


def test_pattern_c_wearables_tablets_grow_about_5_percent_monthly(seeded):
    revenue = [r for _, r in monthly_revenue(seeded[0], category="Wearables & Tablets")]
    for previous, current in zip(revenue, revenue[1:]):
        assert 0.03 <= current / previous - 1 <= 0.07


@pytest.mark.parametrize(
    "year_months",
    [
        [(2026, m) for m in range(1, 7)],
        [(2025, m) for m in range(7, 12)],
    ],
    ids=["2026-01x6", "2025-07x5"],
)
def test_pattern_c_wearables_tablets_excluded_from_dip_and_recovery_factors(year_months):
    # Checks the rule itself on the noise-free targets: every month, including the dip
    # (month 3) and recovery (month 4), is exactly base units x 1.05 ** (month - 1).
    targets = monthly_unit_targets(year_months)
    assert len(targets) == len(year_months)
    for position, month in enumerate(targets, start=1):
        for product in ("Smartwatch", "Tablet 11"):
            for i, region in enumerate(REGIONS):
                expected = BASE_MONTHLY_UNITS[product][i] * 1.05 ** (position - 1)
                assert month[product, region] == pytest.approx(expected, rel=1e-9)


def test_pattern_d_month4_within_5_percent_of_month2(seeded):
    revenue = [r for _, r in monthly_revenue(seeded[0])]
    assert abs(revenue[3] / revenue[1] - 1) <= 0.05


# --- Validation ------------------------------------------------------------


def test_fewer_than_4_months_raises():
    with pytest.raises(ValueError, match="months"):
        generate_sales(42, "2026-01", 3)


@pytest.mark.parametrize("start", ["2026-13", "2026-00", "2026-1", "January", ""])
def test_invalid_start_raises(start):
    with pytest.raises(ValueError, match="start"):
        generate_sales(42, start, 6)


def test_invalid_arguments_do_not_create_database(tmp_path):
    db_path = tmp_path / "test.db"
    with pytest.raises(ValueError):
        seed_database(db_path, months=3)
    assert not db_path.exists()
