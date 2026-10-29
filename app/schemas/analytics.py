"""Response models for the /analytics endpoints.

Units:
  - Money is always integer cents (revenue_cents, change_cents). 1999 means $19.99.
  - Percentages are Decimal rounded to 1 decimal place. Pydantic serializes Decimal as a
    JSON string, so clients receive "-17.2" (not the float -17.2). Strings keep the exact
    value the server computed; a JSON float could arrive as -17.199999999999999.
    A percentage is null when it is undefined (no baseline, or zero total change).

Coverage: every response for a period says which requested months have no data, computed
by app/services/coverage.py (the same logic the LLM tools use). uncovered_months lists at
most 24 months; uncovered_month_count always gives the full number.
"""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

# Allowed values for the dimension query parameter. FastAPI rejects anything else with 422.
# tests/test_api_analytics.py checks these stay equal to the service's dimension tuples.
BreakdownDimension = Literal["product", "region", "category"]
ChangeDimension = Literal["product", "region", "category", "product_region"]


class RowModel(BaseModel):
    # Lets rows be built straight from the service's frozen dataclasses.
    model_config = ConfigDict(from_attributes=True)


class PeriodOut(BaseModel):
    start: str  # YYYY-MM
    end: str  # YYYY-MM, inclusive


class DataCoverageOut(RowModel):
    first_month: str  # YYYY-MM, first month with sales
    last_month: str  # YYYY-MM, last month with sales


class SinglePeriodResponse(BaseModel):
    period: PeriodOut
    data_coverage: DataCoverageOut | None  # null if there are no sales at all
    period_fully_covered: bool
    uncovered_months: list[str]  # requested months outside the data, at most 24; [] if fully covered
    uncovered_month_count: int  # all uncovered months, including any not listed


class TwoPeriodResponse(BaseModel):
    period_a: PeriodOut
    period_b: PeriodOut
    data_coverage: DataCoverageOut | None
    period_a_fully_covered: bool
    period_a_uncovered_months: list[str]
    period_a_uncovered_month_count: int
    period_b_fully_covered: bool
    period_b_uncovered_months: list[str]
    period_b_uncovered_month_count: int


class MetaResponse(BaseModel):
    first_date: str | None  # YYYY-MM-DD; null if there are no sales
    last_date: str | None
    products: list[str]
    regions: list[str]
    categories: list[str]


class MonthRevenueRow(RowModel):
    month: str  # YYYY-MM
    revenue_cents: int
    units: int
    orders: int


class RevenueByMonthResponse(SinglePeriodResponse):
    rows: list[MonthRevenueRow]


class BreakdownRowOut(RowModel):
    label: str
    revenue_cents: int
    units: int


class BreakdownResponse(SinglePeriodResponse):
    dimension: BreakdownDimension
    rows: list[BreakdownRowOut]


class TopProductsResponse(SinglePeriodResponse):
    rows: list[BreakdownRowOut]


class CompareResponse(TwoPeriodResponse):
    revenue_a_cents: int
    revenue_b_cents: int
    change_cents: int  # revenue_b_cents - revenue_a_cents
    pct_change: Decimal | None  # "-17.2" means -17.2%; null if period A had no revenue


class DriverRow(RowModel):
    label: str
    revenue_a_cents: int
    revenue_b_cents: int
    change_cents: int
    share_of_total_change: Decimal | None  # "71.6" means 71.6% of the total change; null if it is 0


class DriversResponse(TwoPeriodResponse):
    dimension: ChangeDimension
    rows: list[DriverRow]
