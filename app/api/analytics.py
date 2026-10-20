"""Read-only analytics endpoints.

Each endpoint only validates input, calls app/services/analytics.py and returns a
response model. No SQL and no calculations here.
"""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_db
from app.api.errors import APIError
from app.schemas.analytics import (
    BreakdownDimension,
    BreakdownResponse,
    ChangeDimension,
    CompareResponse,
    DriversResponse,
    MetaResponse,
    PeriodOut,
    RevenueByMonthResponse,
    TopProductsResponse,
)
from app.services import analytics
from app.services.analytics import MAX_TOP_LIMIT, Period

router = APIRouter(prefix="/analytics", tags=["analytics"])

# Anchored: pydantic matches patterns anywhere in the string unless ^ and $ are given.
# [0-9], not \d: pydantic's regex engine treats \d as any Unicode digit.
Month = Annotated[str, Query(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$", description="YYYY-MM", examples=["2026-03"])]
Limit = Annotated[int, Query(ge=1, le=MAX_TOP_LIMIT)]
Db = Annotated[sqlite3.Connection, Depends(get_db)]


def _period(start: str, end: str, names: str) -> Period:
    """Build a Period, turning its ValueError (e.g. start after end) into a validation_error.

    Only this constructor is wrapped: a ValueError from anywhere else is our bug and
    should surface as a 500, not be disguised as a client error.
    """
    try:
        return Period(start, end)
    except ValueError as exc:
        raise APIError(
            422,
            "validation_error",
            f"Invalid period ({names}): {exc}",
            details=[{"location": "query", "field": names, "message": str(exc), "type": "period_order"}],
        ) from exc


def _echo(period: Period) -> PeriodOut:
    return PeriodOut(start=period.start_month, end=period.end_month)


@router.get("/meta", response_model=MetaResponse)
def meta(conn: Db) -> MetaResponse:
    """Date range of the data and the available products and regions."""
    date_range = analytics.data_date_range(conn)
    return MetaResponse(
        first_date=date_range.first_date if date_range else None,
        last_date=date_range.last_date if date_range else None,
        products=analytics.list_products(conn),
        regions=analytics.list_regions(conn),
    )


@router.get("/revenue-by-month", response_model=RevenueByMonthResponse)
def revenue_by_month(conn: Db, start: Month, end: Month) -> RevenueByMonthResponse:
    period = _period(start, end, "start..end")
    return RevenueByMonthResponse(period=_echo(period), rows=analytics.revenue_by_month(conn, period))


@router.get("/breakdown", response_model=BreakdownResponse)
def breakdown(conn: Db, start: Month, end: Month, dimension: BreakdownDimension) -> BreakdownResponse:
    period = _period(start, end, "start..end")
    rows = analytics.revenue_breakdown(conn, period, dimension)
    return BreakdownResponse(period=_echo(period), dimension=dimension, rows=rows)


@router.get("/compare", response_model=CompareResponse)
def compare(conn: Db, a_start: Month, a_end: Month, b_start: Month, b_end: Month) -> CompareResponse:
    period_a = _period(a_start, a_end, "a_start..a_end")
    period_b = _period(b_start, b_end, "b_start..b_end")
    result = analytics.compare_periods(conn, period_a, period_b)
    return CompareResponse(
        period_a=_echo(period_a),
        period_b=_echo(period_b),
        revenue_a_cents=result.revenue_a_cents,
        revenue_b_cents=result.revenue_b_cents,
        change_cents=result.change_cents,
        pct_change=result.pct_change,
    )


@router.get("/drivers", response_model=DriversResponse)
def drivers(
    conn: Db,
    a_start: Month,
    a_end: Month,
    b_start: Month,
    b_end: Month,
    dimension: ChangeDimension,
    limit: Limit = 5,
) -> DriversResponse:
    """Labels that drove the revenue change from period A to period B, biggest first."""
    period_a = _period(a_start, a_end, "a_start..a_end")
    period_b = _period(b_start, b_end, "b_start..b_end")
    rows = analytics.change_drivers(conn, period_a, period_b, dimension, limit=limit)
    return DriversResponse(period_a=_echo(period_a), period_b=_echo(period_b), dimension=dimension, rows=rows)


@router.get("/top-products", response_model=TopProductsResponse)
def top_products(conn: Db, start: Month, end: Month, limit: Limit = 5) -> TopProductsResponse:
    period = _period(start, end, "start..end")
    return TopProductsResponse(period=_echo(period), rows=analytics.top_products(conn, period, limit))
