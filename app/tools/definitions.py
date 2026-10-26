"""The six analytics tools: the only way an LLM can ever touch data.

Each tool is a name, a description written for the LLM, a Pydantic argument model and a
handler. Handlers receive already-validated arguments and call app/services/analytics
only (never app/db/queries.py), so every query stays parameterized.

Argument models:
  - extra="forbid": an unknown field is an error, not silently ignored.
  - strict=True: no type coercion. "5" is not accepted as 5, and true is not accepted as 1.
    A wrong type comes back as invalid_arguments and the model can retry with the right one.
  - Months reuse Period's own YYYY-MM rule (fullmatch, [0-9] only), so the tools and the
    service can never disagree about what a valid month is.
  - Dimension enums are built from the service's constants, so the lists cannot drift.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from itertools import chain, islice
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from app.services import analytics, metrics
from app.services.analytics import BREAKDOWN_DIMENSIONS, CHANGE_DIMENSIONS, MAX_TOP_LIMIT, Period
from app.tools.results import format_pct, format_pct_abs, with_display

# Only for the JSON schema the LLM reads. Validation is done by _check_month, because
# Pydantic's own `pattern` searches the string instead of matching the whole of it.
_MONTH_SCHEMA_PATTERN = "^[0-9]{4}-(0[1-9]|1[0-2])$"
DEFAULT_LIMIT = 5
# A long period far outside the data could list thousands of months; the count covers the rest.
MAX_LISTED_UNCOVERED_MONTHS = 24


def _check_month(value: str) -> str:
    try:
        Period.of_month(value)  # the service's own rule
    except ValueError:
        # Our own message: Period's message includes the raw value, which must not be echoed.
        raise ValueError("must be a month in YYYY-MM format with month 01-12, e.g. 2026-03") from None
    return value


Month = Annotated[
    str,
    AfterValidator(_check_month),
    Field(json_schema_extra={"pattern": _MONTH_SCHEMA_PATTERN, "examples": ["2026-03"]}),
]
Limit = Annotated[int, Field(ge=1, le=MAX_TOP_LIMIT, description=f"How many rows to return (1-{MAX_TOP_LIMIT}).")]
# Literal[("a", "b")] is the same type as Literal["a", "b"].
BreakdownDimension = Literal[BREAKDOWN_DIMENSIONS]  # type: ignore[valid-type]
ChangeDimension = Literal[CHANGE_DIMENSIONS]  # type: ignore[valid-type]


# --- argument models -------------------------------------------------------


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SinglePeriodArguments(ToolArguments):
    start_month: Month = Field(description="First month of the period, YYYY-MM, inclusive.")
    end_month: Month = Field(description="Last month of the period, YYYY-MM, inclusive. Same as start_month for one month.")

    # A model_validator turns start-after-end into a normal validation error with our own
    # message, instead of a ValueError from Period inside the handler.
    @model_validator(mode="after")
    def _check_order(self) -> "SinglePeriodArguments":
        if self.start_month > self.end_month:  # zero-padded YYYY-MM sorts in calendar order
            raise ValueError("start_month must not be after end_month")
        return self

    def period(self) -> Period:
        return Period(self.start_month, self.end_month)


class TwoPeriodArguments(ToolArguments):
    period_a_start: Month = Field(description="First month of the earlier (baseline) period A, YYYY-MM.")
    period_a_end: Month = Field(description="Last month of period A, YYYY-MM, inclusive.")
    period_b_start: Month = Field(description="First month of the later period B, YYYY-MM.")
    period_b_end: Month = Field(description="Last month of period B, YYYY-MM, inclusive.")

    @model_validator(mode="after")
    def _check_order(self) -> "TwoPeriodArguments":
        if self.period_a_start > self.period_a_end:
            raise ValueError("period_a_start must not be after period_a_end")
        if self.period_b_start > self.period_b_end:
            raise ValueError("period_b_start must not be after period_b_end")
        return self

    def period_a(self) -> Period:
        return Period(self.period_a_start, self.period_a_end)

    def period_b(self) -> Period:
        return Period(self.period_b_start, self.period_b_end)


class GetDataOverviewArguments(ToolArguments):
    pass


class GetRevenueByMonthArguments(SinglePeriodArguments):
    pass


class GetRevenueBreakdownArguments(SinglePeriodArguments):
    dimension: BreakdownDimension = Field(description="What to group revenue by.")


class ComparePeriodsArguments(TwoPeriodArguments):
    pass


class GetChangeDriversArguments(TwoPeriodArguments):
    dimension: ChangeDimension = Field(
        description='What to break the change down by. "product_region" gives labels like "StudioBook 16 / West".'
    )
    limit: Limit = DEFAULT_LIMIT


class GetTopProductsArguments(SinglePeriodArguments):
    limit: Limit = DEFAULT_LIMIT


# --- data coverage ---------------------------------------------------------


def _month_index(month: str) -> int:
    return int(month[:4]) * 12 + int(month[5:7]) - 1


def _month_name(index: int) -> str:
    year, month0 = divmod(index, 12)
    return f"{year:04d}-{month0 + 1:02d}"


def _data_coverage(conn: sqlite3.Connection) -> dict[str, str] | None:
    """First and last month that have sales, or None for an empty database."""
    date_range = analytics.data_date_range(conn)
    if date_range is None:
        return None
    return {"first_month": date_range.first_date[:7], "last_month": date_range.last_date[:7]}


def _coverage_fields(
    period: Period, coverage: dict[str, str] | None, flag_key: str, months_key: str
) -> dict[str, Any]:
    """Is every requested month inside the data's coverage? Computed here so the LLM never has to.

    "Uncovered" means outside the first..last month of the data. The seeded data has sales
    in every month of that range, so here this is the same as "has no sales".
    """
    start, end = _month_index(period.start_month), _month_index(period.end_month)
    if coverage is None:
        uncovered = [range(start, end + 1)]
    else:
        first, last = _month_index(coverage["first_month"]), _month_index(coverage["last_month"])
        # Coverage is one contiguous block, so uncovered months are at most a run before it and a run after it.
        uncovered = [range(start, min(end, first - 1) + 1), range(max(start, last + 1), end + 1)]
    count = sum(len(run) for run in uncovered)
    fields: dict[str, Any] = {flag_key: count == 0}
    if count:
        listed = islice(chain.from_iterable(uncovered), MAX_LISTED_UNCOVERED_MONTHS)
        fields[months_key] = [_month_name(index) for index in listed]
        if count > MAX_LISTED_UNCOVERED_MONTHS:
            fields[months_key.removesuffix("months") + "month_count"] = count
    return fields


def _period_out(period: Period) -> dict[str, str]:
    return {"start_month": period.start_month, "end_month": period.end_month}


def _single_period_header(conn: sqlite3.Connection, period: Period) -> dict[str, Any]:
    coverage = _data_coverage(conn)
    return {
        "period": _period_out(period),
        "data_coverage": coverage,
        **_coverage_fields(period, coverage, "period_fully_covered", "uncovered_months"),
    }


def _two_period_header(conn: sqlite3.Connection, period_a: Period, period_b: Period) -> dict[str, Any]:
    coverage = _data_coverage(conn)
    return {
        "period_a": _period_out(period_a),
        "period_b": _period_out(period_b),
        "data_coverage": coverage,
        **_coverage_fields(period_a, coverage, "period_a_fully_covered", "period_a_uncovered_months"),
        **_coverage_fields(period_b, coverage, "period_b_fully_covered", "period_b_uncovered_months"),
    }


# --- handlers --------------------------------------------------------------


def _breakdown_row(row: analytics.BreakdownRow) -> dict[str, Any]:
    return with_display({"label": row.label, "revenue_cents": row.revenue_cents, "units": row.units})


def _get_data_overview(conn: sqlite3.Connection, args: GetDataOverviewArguments) -> dict[str, Any]:
    date_range = analytics.data_date_range(conn)
    return {
        "data_coverage": _data_coverage(conn),
        "first_date": date_range.first_date if date_range else None,
        "last_date": date_range.last_date if date_range else None,
        "products": analytics.list_products(conn),
        "regions": analytics.list_regions(conn),
        "categories": analytics.list_categories(conn),
    }


def _get_revenue_by_month(conn: sqlite3.Connection, args: GetRevenueByMonthArguments) -> dict[str, Any]:
    period = args.period()
    rows = analytics.revenue_by_month(conn, period)
    return {
        **_single_period_header(conn, period),
        "rows": [
            with_display({"month": r.month, "revenue_cents": r.revenue_cents, "units": r.units, "orders": r.orders})
            for r in rows
        ],
    }


def _get_revenue_breakdown(conn: sqlite3.Connection, args: GetRevenueBreakdownArguments) -> dict[str, Any]:
    period = args.period()
    rows = analytics.revenue_breakdown(conn, period, args.dimension)
    return {
        **_single_period_header(conn, period),
        "dimension": args.dimension,
        "rows": [_breakdown_row(r) for r in rows],
    }


def _compare_periods(conn: sqlite3.Connection, args: ComparePeriodsArguments) -> dict[str, Any]:
    period_a, period_b = args.period_a(), args.period_b()
    comparison = analytics.compare_periods(conn, period_a, period_b)
    return {
        **_two_period_header(conn, period_a, period_b),
        **with_display(
            {
                "revenue_a_cents": comparison.revenue_a_cents,
                "revenue_b_cents": comparison.revenue_b_cents,
                "change_cents": comparison.change_cents,
            }
        ),
        "pct_change": format_pct(comparison.pct_change),
        "pct_change_abs": format_pct_abs(comparison.pct_change),
    }


def _get_change_drivers(conn: sqlite3.Connection, args: GetChangeDriversArguments) -> dict[str, Any]:
    period_a, period_b = args.period_a(), args.period_b()
    # All drivers, truncated here: their sum is the total change, which the shares are relative to.
    drivers = analytics.change_drivers(conn, period_a, period_b, args.dimension)
    # Every sale has a product and a region, so summing all labels gives the period totals.
    total_a = sum(d.revenue_a_cents for d in drivers)
    total_b = sum(d.revenue_b_cents for d in drivers)
    pct_change = metrics.pct_change(total_a, total_b)
    return {
        **_two_period_header(conn, period_a, period_b),
        "dimension": args.dimension,
        **with_display(
            {"total_revenue_a_cents": total_a, "total_revenue_b_cents": total_b, "total_change_cents": total_b - total_a}
        ),
        "pct_change": format_pct(pct_change),
        "pct_change_abs": format_pct_abs(pct_change),
        "driver_count": len(drivers),
        "rows": [
            {
                **with_display(
                    {
                        "label": d.label,
                        "revenue_a_cents": d.revenue_a_cents,
                        "revenue_b_cents": d.revenue_b_cents,
                        "change_cents": d.change_cents,
                    }
                ),
                "share_of_total_change": format_pct(d.share_of_total_change),
            }
            for d in drivers[: args.limit]
        ],
    }


def _get_top_products(conn: sqlite3.Connection, args: GetTopProductsArguments) -> dict[str, Any]:
    period = args.period()
    rows = analytics.top_products(conn, period, args.limit)
    return {**_single_period_header(conn, period), "rows": [_breakdown_row(r) for r in rows]}


# --- registry --------------------------------------------------------------


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    arguments_model: type[ToolArguments]
    handler: Callable[[sqlite3.Connection, Any], dict[str, Any]]


_COVERAGE_NOTE = (
    " The result includes data_coverage (first and last month with data) and whether the requested"
    " period is fully covered; if it is not, say which months have no data instead of guessing."
)
_MONEY_NOTE = (
    " Quote money from the *_display or *_abs_display fields and percentages from the pct/share"
    " fields exactly as written; never compute or reformat a number."
)

_TOOL_LIST = [
    Tool(
        name="get_data_overview",
        description=(
            "Describes the dataset: the first and last month with sales data, and the names of all"
            " products, regions and categories. Use it first when you need valid names or the"
            " available date range, or when a question mentions a month or name you are unsure about."
            ' Example questions: "What data do you have?", "Which regions are there?"'
        ),
        arguments_model=GetDataOverviewArguments,
        handler=_get_data_overview,
    ),
    Tool(
        name="get_revenue_by_month",
        description=(
            "Total revenue, units and orders for each month in a period, in calendar order."
            " Use it for trends over time. Months with no sales are left out of the rows."
            ' Example questions: "How did revenue develop from January to June 2026?",'
            ' "What was revenue in March 2026?"' + _COVERAGE_NOTE + _MONEY_NOTE
        ),
        arguments_model=GetRevenueByMonthArguments,
        handler=_get_revenue_by_month,
    ),
    Tool(
        name="get_revenue_breakdown",
        description=(
            "Revenue and units per product, region or category over a period, largest first."
            " Use it to see how revenue is split within one period."
            ' Example questions: "Which region had the most revenue in Q1 2026?",'
            ' "Revenue by category in May 2026?"' + _COVERAGE_NOTE + _MONEY_NOTE
        ),
        arguments_model=GetRevenueBreakdownArguments,
        handler=_get_revenue_breakdown,
    ),
    Tool(
        name="compare_periods",
        description=(
            "Total revenue in period A versus period B, the change (B minus A) and the percentage"
            " change from A to B. pct_change is null when period A had no revenue."
            ' Example questions: "How did March 2026 compare with February 2026?",'
            ' "Was Q2 better than Q1?"' + _COVERAGE_NOTE + _MONEY_NOTE
        ),
        arguments_model=ComparePeriodsArguments,
        handler=_compare_periods,
    ),
    Tool(
        name="get_change_drivers",
        description=(
            "Explains a revenue change between period A and period B: the total revenue of each"
            " period, the total change and its percentage, and which products, regions,"
            " categories or product/region pairs moved the most, largest absolute change first."
            " share_of_total_change is a percentage of the total change across ALL labels (not just"
            " the rows returned); it is negative for a label that moved against the total and can"
            " exceed 100 when others partly offset it."
            ' Example questions: "Why did revenue drop in March 2026?",'
            ' "What drove the change between Q1 and Q2?"' + _COVERAGE_NOTE + _MONEY_NOTE
        ),
        arguments_model=GetChangeDriversArguments,
        handler=_get_change_drivers,
    ),
    Tool(
        name="get_top_products",
        description=(
            "The best-selling products by revenue over a period, largest first."
            ' Example questions: "What were our top 3 products in Q1 2026?",'
            ' "Best seller in June 2026?"' + _COVERAGE_NOTE + _MONEY_NOTE
        ),
        arguments_model=GetTopProductsArguments,
        handler=_get_top_products,
    ),
]
TOOLS: dict[str, Tool] = {tool.name: tool for tool in _TOOL_LIST}


def tool_definitions() -> list[dict[str, Any]]:
    """Provider-neutral tool list: name, description and JSON schema of the arguments."""
    definitions = []
    for tool in _TOOL_LIST:
        schema = tool.arguments_model.model_json_schema()
        schema.setdefault("required", [])  # Pydantic omits it when no field is required
        definitions.append({"name": tool.name, "description": tool.description, "input_schema": schema})
    return definitions
