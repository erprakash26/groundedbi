"""Data coverage: which requested months fall outside the data. Shared by the tools and the API.

"Uncovered" means outside the first..last month that has sales. The seeded data has sales in
every month of that range, so here this is the same as "has no sales".
"""

import sqlite3
from dataclasses import dataclass
from itertools import chain, islice

from app.services import analytics
from app.services.analytics import Period

# A long period far outside the data could list thousands of months; the count covers the rest.
MAX_LISTED_UNCOVERED_MONTHS = 24


@dataclass(frozen=True)
class DataCoverage:
    first_month: str  # YYYY-MM, first month with sales
    last_month: str  # YYYY-MM, last month with sales


@dataclass(frozen=True)
class PeriodCoverage:
    fully_covered: bool
    uncovered_months: list[str]  # YYYY-MM, in order, at most MAX_LISTED_UNCOVERED_MONTHS
    uncovered_month_count: int  # all uncovered months, including any not listed


def _month_index(month: str) -> int:
    return int(month[:4]) * 12 + int(month[5:7]) - 1


def _month_name(index: int) -> str:
    year, month0 = divmod(index, 12)
    return f"{year:04d}-{month0 + 1:02d}"


def data_coverage(conn: sqlite3.Connection) -> DataCoverage | None:
    """First and last month that have sales, or None for an empty database."""
    date_range = analytics.data_date_range(conn)
    if date_range is None:
        return None
    return DataCoverage(date_range.first_date[:7], date_range.last_date[:7])


def period_coverage(period: Period, coverage: DataCoverage | None) -> PeriodCoverage:
    """Is every month of the period inside the data's coverage? Computed so callers never compare months."""
    start, end = _month_index(period.start_month), _month_index(period.end_month)
    if coverage is None:
        uncovered = [range(start, end + 1)]
    else:
        first, last = _month_index(coverage.first_month), _month_index(coverage.last_month)
        # Coverage is one contiguous block, so uncovered months are at most a run before it and a run after it.
        uncovered = [range(start, min(end, first - 1) + 1), range(max(start, last + 1), end + 1)]
    count = sum(len(run) for run in uncovered)
    listed = islice(chain.from_iterable(uncovered), MAX_LISTED_UNCOVERED_MONTHS)
    return PeriodCoverage(count == 0, [_month_name(index) for index in listed], count)
