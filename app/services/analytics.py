"""Analytics service: the layer the API and future tools call (never app/db/queries.py directly).

Calls the cents-level queries and adds percentages using metrics.py. The query dataclasses
and constants are re-exported here so nothing above this layer needs to import queries.
"""

import sqlite3
from dataclasses import dataclass
from decimal import Decimal

from app.db import queries
from app.db.queries import (
    BREAKDOWN_DIMENSIONS,
    CHANGE_DIMENSIONS,
    MAX_TOP_LIMIT,
    BreakdownRow,
    DateRange,
    MonthRevenue,
    Period,
)
from app.services import metrics

__all__ = [
    "BREAKDOWN_DIMENSIONS",
    "CHANGE_DIMENSIONS",
    "MAX_TOP_LIMIT",
    "BreakdownRow",
    "ChangeDriver",
    "DateRange",
    "MonthRevenue",
    "Period",
    "PeriodComparison",
    "change_drivers",
    "compare_periods",
    "data_date_range",
    "list_categories",
    "list_products",
    "list_regions",
    "revenue_breakdown",
    "revenue_by_month",
    "top_products",
]


@dataclass(frozen=True)
class PeriodComparison:
    revenue_a_cents: int
    revenue_b_cents: int
    change_cents: int  # revenue_b_cents - revenue_a_cents
    pct_change: Decimal | None  # percentage: Decimal("-17.2") means -17.2%; None if period A had no revenue


@dataclass(frozen=True)
class ChangeDriver:
    label: str
    revenue_a_cents: int
    revenue_b_cents: int
    change_cents: int  # revenue_b_cents - revenue_a_cents
    share_of_total_change: Decimal | None  # percentage: Decimal("71.8") means 71.8%; None if total change is 0


# Thin wrappers: no extra logic today, but they keep the service layer the single entry point.


def data_date_range(conn: sqlite3.Connection) -> DateRange | None:
    """First and last sale dates in the data, or None if there are no sales."""
    return queries.data_date_range(conn)


def list_products(conn: sqlite3.Connection) -> list[str]:
    return queries.list_products(conn)


def list_regions(conn: sqlite3.Connection) -> list[str]:
    return queries.list_regions(conn)


def list_categories(conn: sqlite3.Connection) -> list[str]:
    return queries.list_categories(conn)


def revenue_by_month(conn: sqlite3.Connection, period: Period) -> list[MonthRevenue]:
    """One row per month that has sales; [] for a period with no data."""
    return queries.revenue_by_month(conn, period)


def revenue_breakdown(conn: sqlite3.Connection, period: Period, dimension: str) -> list[BreakdownRow]:
    """Revenue per product, region or category, sorted by revenue descending."""
    return queries.revenue_breakdown(conn, period, dimension)


def top_products(conn: sqlite3.Connection, period: Period, limit: int) -> list[BreakdownRow]:
    """The `limit` best-selling products by revenue (1 <= limit <= MAX_TOP_LIMIT)."""
    return queries.top_products(conn, period, limit)


def compare_periods(conn: sqlite3.Connection, period_a: Period, period_b: Period) -> PeriodComparison:
    """Total revenue in period A vs period B, with the percentage change from A to B."""
    revenue_a = queries.period_totals(conn, period_a).revenue_cents
    revenue_b = queries.period_totals(conn, period_b).revenue_cents
    return PeriodComparison(revenue_a, revenue_b, revenue_b - revenue_a, metrics.pct_change(revenue_a, revenue_b))


def change_drivers(
    conn: sqlite3.Connection, period_a: Period, period_b: Period, dimension: str, limit: int | None = None
) -> list[ChangeDriver]:
    """Which labels drove the revenue change from A to B, sorted by absolute change descending.

    share_of_total_change is a percentage of the total change: 71.8 means this label
    accounts for 71.8% of it. A label moving against the total gets a negative share,
    and shares can exceed 100 when other labels partly offset it.
    dimension is "product", "region", "category" or "product_region".
    limit keeps only the first `limit` drivers (None keeps all). Shares are always
    computed against the total change of ALL labels, before truncating.
    """
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 1):
        raise ValueError(f"limit must be a positive integer or None, got {limit!r}")
    changes = queries.revenue_changes(conn, period_a, period_b, dimension)
    # Every sale has a product and a region, so the per-label changes add up to the total change.
    total_change = sum(change.change_cents for change in changes)
    drivers = [
        ChangeDriver(
            change.label,
            change.revenue_a_cents,
            change.revenue_b_cents,
            change.change_cents,
            metrics.share(change.change_cents, total_change),
        )
        for change in changes
    ]
    return drivers if limit is None else drivers[:limit]
