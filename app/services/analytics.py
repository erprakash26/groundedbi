"""Analytics service: the layer future tools call (never app/db/queries.py directly).

Calls the cents-level queries and adds percentages using metrics.py.
"""

import sqlite3
from dataclasses import dataclass
from decimal import Decimal

from app.db import queries
from app.db.queries import Period
from app.services import metrics

__all__ = ["ChangeDriver", "Period", "PeriodComparison", "change_drivers", "compare_periods"]


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


def compare_periods(conn: sqlite3.Connection, period_a: Period, period_b: Period) -> PeriodComparison:
    """Total revenue in period A vs period B, with the percentage change from A to B."""
    revenue_a = queries.period_totals(conn, period_a).revenue_cents
    revenue_b = queries.period_totals(conn, period_b).revenue_cents
    return PeriodComparison(revenue_a, revenue_b, revenue_b - revenue_a, metrics.pct_change(revenue_a, revenue_b))


def change_drivers(
    conn: sqlite3.Connection, period_a: Period, period_b: Period, dimension: str
) -> list[ChangeDriver]:
    """Which labels drove the revenue change from A to B, sorted by absolute change descending.

    share_of_total_change is a percentage of the total change: 71.8 means this label
    accounts for 71.8% of it. A label moving against the total gets a negative share,
    and shares can exceed 100 when other labels partly offset it.
    dimension is "product", "region", "category" or "product_region".
    """
    changes = queries.revenue_changes(conn, period_a, period_b, dimension)
    # Every sale has a product and a region, so the per-label changes add up to the total change.
    total_change = sum(change.change_cents for change in changes)
    return [
        ChangeDriver(
            change.label,
            change.revenue_a_cents,
            change.revenue_b_cents,
            change.change_cents,
            metrics.share(change.change_cents, total_change),
        )
        for change in changes
    ]
