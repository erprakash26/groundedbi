"""Read-only analytics queries over the sales database.

Every function takes a sqlite3 connection as its first argument and returns frozen
dataclasses (or plain names/ints), never raw rows. Results are cents and counts only;
percentages are added by app/services/analytics.py.

SQL safety:
  - Every value is passed as a ? placeholder.
  - Dimensions (which column to group by) cannot be placeholders, so each allowed dimension
    maps to a fixed SQL fragment. The full SQL text for every dimension is built once at
    import time from those fixed fragments; at call time the only step is a dictionary
    lookup, and unknown dimensions raise ValueError. Input never reaches string formatting.
"""

import re
import sqlite3
from dataclasses import dataclass

_MONTH_PATTERN = re.compile(r"\d{4}-(0[1-9]|1[0-2])")
MAX_TOP_LIMIT = 20


@dataclass(frozen=True)
class Period:
    """An inclusive range of whole months, e.g. Period("2026-02", "2026-03") is Feb + Mar."""

    start_month: str  # YYYY-MM
    end_month: str  # YYYY-MM, inclusive

    def __post_init__(self) -> None:
        for field_name in ("start_month", "end_month"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not _MONTH_PATTERN.fullmatch(value):
                raise ValueError(f"{field_name} must be YYYY-MM with month 01-12, got {value!r}")
        # Zero-padded YYYY-MM strings sort in calendar order, so string comparison is correct.
        if self.start_month > self.end_month:
            raise ValueError(f"start_month {self.start_month} is after end_month {self.end_month}")

    @classmethod
    def of_month(cls, month: str) -> "Period":
        """A period covering a single month."""
        return cls(month, month)

    def date_bounds(self) -> tuple[str, str]:
        """Return (first day of start month, first day of the month after end month).

        Queries filter with a half-open range: sale_date >= first AND sale_date < after_end.
        Why half-open instead of BETWEEN first AND last_day:
          - no need to compute the last day of a month (28, 29, 30 or 31);
          - consecutive periods meet exactly, so no day is lost or counted twice;
          - it stays correct if sale_date ever gains a time part ('2026-03-31 14:00'
            is > '2026-03-31' and would fall outside a BETWEEN range).
        """
        year, month = int(self.end_month[:4]), int(self.end_month[5:7])
        after_end = f"{year + 1:04d}-01-01" if month == 12 else f"{year:04d}-{month + 1:02d}-01"
        return f"{self.start_month}-01", after_end


@dataclass(frozen=True)
class DateRange:
    first_date: str  # YYYY-MM-DD
    last_date: str  # YYYY-MM-DD


@dataclass(frozen=True)
class MonthRevenue:
    month: str  # YYYY-MM
    revenue_cents: int
    units: int
    orders: int  # each sales row is one order


@dataclass(frozen=True)
class BreakdownRow:
    label: str
    revenue_cents: int
    units: int


@dataclass(frozen=True)
class PeriodTotals:
    revenue_cents: int
    units: int
    orders: int


@dataclass(frozen=True)
class RevenueChange:
    label: str
    revenue_a_cents: int
    revenue_b_cents: int
    change_cents: int  # revenue_b_cents - revenue_a_cents


# Fixed SQL expressions for each allowed dimension. These are the only strings ever
# inserted into SQL text, and only at import time.
_LABEL_SQL = {
    "product": "p.name",
    "region": "r.name",
    "category": "p.category",
    "product_region": "p.name || ' / ' || r.name",
}
BREAKDOWN_DIMENSIONS = ("product", "region", "category")
CHANGE_DIMENSIONS = tuple(_LABEL_SQL)

_BREAKDOWN_TEMPLATE = """
SELECT {label} AS label, SUM(s.revenue_cents) AS revenue_cents, SUM(s.quantity) AS units
FROM sales s
JOIN products p ON p.product_id = s.product_id
JOIN regions r ON r.region_id = s.region_id
WHERE s.sale_date >= ? AND s.sale_date < ?
GROUP BY label
ORDER BY revenue_cents DESC, label ASC
"""

# Sums both periods side by side, so a label sold in only one period still gets a row.
_CHANGE_TEMPLATE = """
SELECT
    {label} AS label,
    SUM(CASE WHEN s.sale_date >= ? AND s.sale_date < ? THEN s.revenue_cents ELSE 0 END) AS revenue_a_cents,
    SUM(CASE WHEN s.sale_date >= ? AND s.sale_date < ? THEN s.revenue_cents ELSE 0 END) AS revenue_b_cents
FROM sales s
JOIN products p ON p.product_id = s.product_id
JOIN regions r ON r.region_id = s.region_id
WHERE (s.sale_date >= ? AND s.sale_date < ?) OR (s.sale_date >= ? AND s.sale_date < ?)
GROUP BY label
ORDER BY ABS(revenue_b_cents - revenue_a_cents) DESC, label ASC
"""

_BREAKDOWN_SQL = {dim: _BREAKDOWN_TEMPLATE.format(label=_LABEL_SQL[dim]) for dim in BREAKDOWN_DIMENSIONS}
_CHANGE_SQL = {dim: _CHANGE_TEMPLATE.format(label=_LABEL_SQL[dim]) for dim in CHANGE_DIMENSIONS}
_TOP_PRODUCTS_SQL = _BREAKDOWN_SQL["product"] + "LIMIT ?\n"


def _sql_for_dimension(sql_by_dimension: dict[str, str], dimension: str) -> str:
    try:
        return sql_by_dimension[dimension]
    except (KeyError, TypeError):  # TypeError: unhashable input such as a list
        raise ValueError(f"dimension must be one of {list(sql_by_dimension)}, got {dimension!r}") from None


# Rows are unpacked by position, so these functions work whatever row_factory the connection uses.


def data_date_range(conn: sqlite3.Connection) -> DateRange | None:
    """First and last sale dates present in the data, or None if there are no sales."""
    first_date, last_date = conn.execute("SELECT MIN(sale_date), MAX(sale_date) FROM sales").fetchone()
    if first_date is None:
        return None
    return DateRange(first_date, last_date)


def list_products(conn: sqlite3.Connection) -> list[str]:
    return [name for (name,) in conn.execute("SELECT name FROM products ORDER BY name")]


def list_regions(conn: sqlite3.Connection) -> list[str]:
    return [name for (name,) in conn.execute("SELECT name FROM regions ORDER BY name")]


def revenue_by_month(conn: sqlite3.Connection, period: Period) -> list[MonthRevenue]:
    """One row per month that has sales. A period with no data returns []."""
    rows = conn.execute(
        "SELECT substr(sale_date, 1, 7) AS month, SUM(revenue_cents), SUM(quantity), COUNT(*) "
        "FROM sales WHERE sale_date >= ? AND sale_date < ? "
        "GROUP BY month ORDER BY month",
        period.date_bounds(),
    )
    return [MonthRevenue(month, revenue, units, orders) for month, revenue, units, orders in rows]


def period_totals(conn: sqlite3.Connection, period: Period) -> PeriodTotals:
    """Total revenue, units and orders in the period (zeros if it has no sales)."""
    revenue, units, orders = conn.execute(
        "SELECT COALESCE(SUM(revenue_cents), 0), COALESCE(SUM(quantity), 0), COUNT(*) "
        "FROM sales WHERE sale_date >= ? AND sale_date < ?",
        period.date_bounds(),
    ).fetchone()
    return PeriodTotals(revenue, units, orders)


def revenue_breakdown(conn: sqlite3.Connection, period: Period, dimension: str) -> list[BreakdownRow]:
    """Revenue and units per product, region or category, sorted by revenue descending."""
    sql = _sql_for_dimension(_BREAKDOWN_SQL, dimension)
    return [BreakdownRow(label, revenue, units) for label, revenue, units in conn.execute(sql, period.date_bounds())]


def revenue_changes(
    conn: sqlite3.Connection, period_a: Period, period_b: Period, dimension: str
) -> list[RevenueChange]:
    """Revenue in period A and B per label, sorted by absolute change descending.

    dimension is "product", "region", "category" or "product_region" ("StudioBook 16 / West").
    """
    sql = _sql_for_dimension(_CHANGE_SQL, dimension)
    bounds_a, bounds_b = period_a.date_bounds(), period_b.date_bounds()
    rows = conn.execute(sql, (*bounds_a, *bounds_b, *bounds_a, *bounds_b))
    return [RevenueChange(label, a, b, b - a) for label, a, b in rows]


def top_products(conn: sqlite3.Connection, period: Period, limit: int) -> list[BreakdownRow]:
    """The `limit` best-selling products by revenue (1 <= limit <= MAX_TOP_LIMIT)."""
    # bool is a subclass of int, so True would otherwise pass as 1.
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_TOP_LIMIT:
        raise ValueError(f"limit must be an integer from 1 to {MAX_TOP_LIMIT}, got {limit!r}")
    rows = conn.execute(_TOP_PRODUCTS_SQL, (*period.date_bounds(), limit))
    return [BreakdownRow(label, revenue, units) for label, revenue, units in rows]
