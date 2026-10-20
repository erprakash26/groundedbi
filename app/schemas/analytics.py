"""Response models for the /analytics endpoints.

Units:
  - Money is always integer cents (revenue_cents, change_cents). 1999 means $19.99.
  - Percentages are Decimal rounded to 1 decimal place. Pydantic serializes Decimal as a
    JSON string, so clients receive "-17.2" (not the float -17.2). Strings keep the exact
    value the server computed; a JSON float could arrive as -17.199999999999999.
    A percentage is null when it is undefined (no baseline, or zero total change).
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


class MetaResponse(BaseModel):
    first_date: str | None  # YYYY-MM-DD; null if there are no sales
    last_date: str | None
    products: list[str]
    regions: list[str]


class MonthRevenueRow(RowModel):
    month: str  # YYYY-MM
    revenue_cents: int
    units: int
    orders: int


class RevenueByMonthResponse(BaseModel):
    period: PeriodOut
    rows: list[MonthRevenueRow]


class BreakdownRowOut(RowModel):
    label: str
    revenue_cents: int
    units: int


class BreakdownResponse(BaseModel):
    period: PeriodOut
    dimension: BreakdownDimension
    rows: list[BreakdownRowOut]


class TopProductsResponse(BaseModel):
    period: PeriodOut
    rows: list[BreakdownRowOut]


class CompareResponse(BaseModel):
    period_a: PeriodOut
    period_b: PeriodOut
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


class DriversResponse(BaseModel):
    period_a: PeriodOut
    period_b: PeriodOut
    dimension: ChangeDimension
    rows: list[DriverRow]
