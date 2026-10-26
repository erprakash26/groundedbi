import pytest

from app.llm.question_parser import (
    DEFAULT_TOP_N,
    MonthSpan,
    detect_dimension,
    detect_intent,
    find_names,
    parse_periods,
    parse_top_n,
    previous_span,
)
from app.llm.rule_based import month_label, plan_question

LAST = "2026-06"  # the seeded test data covers 2026-01 to 2026-06
OVERVIEW = {
    "data_coverage": {"first_month": "2026-01", "last_month": LAST},
    "products": ["Phone Max", "StudioBook 16", "Tablet 11"],
    "regions": ["East", "North", "South", "West"],
    "categories": ["Laptops", "Wearables & Tablets"],
}


def one(month: str) -> MonthSpan:
    return MonthSpan(month, month)


# --- parse_periods ----------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("revenue in 2026-03", [one("2026-03")]),
        ("revenue in March 2026", [one("2026-03")]),
        ("revenue in Mar 2026", [one("2026-03")]),
        ("revenue in Mar. 2026", [one("2026-03")]),
        ("revenue in march, 2026", [one("2026-03")]),
        ("revenue in September 2025", [one("2025-09")]),
        ("revenue in sept 2025", [one("2025-09")]),
        ("revenue in Q1 2026", [MonthSpan("2026-01", "2026-03")]),
        ("revenue in q4 2025", [MonthSpan("2025-10", "2025-12")]),
    ],
)
def test_explicit_month_formats(question, expected):
    assert parse_periods(question, LAST) == expected


def test_month_without_year_resolves_against_the_data_not_today():
    # March <= June (the last data month) -> 2026.
    assert parse_periods("Why did revenue drop in March?", LAST) == [one("2026-03")]


def test_month_after_the_last_data_month_resolves_to_the_previous_year():
    # December is after June 2026, so its most recent occurrence in reach of the data is 2025.
    assert parse_periods("Top products in December", LAST) == [one("2025-12")]


def test_quarter_without_year_resolves_against_the_data():
    assert parse_periods("Revenue by region in Q2", LAST) == [MonthSpan("2026-04", "2026-06")]
    assert parse_periods("Revenue by region in Q3", LAST) == [MonthSpan("2025-07", "2025-09")]


def test_single_year_in_question_applies_to_yearless_months():
    assert parse_periods("Compare February and April 2026", "2027-12") == [one("2026-02"), one("2026-04")]


def test_two_different_years_do_not_spread_to_yearless_months():
    # Ambiguous which year March means, so it falls back to the data rule (2026 here).
    assert parse_periods("March vs April 2025 vs May 2024", LAST) == [one("2026-03"), one("2025-04"), one("2024-05")]


def test_bare_year_is_the_whole_year_when_no_month_is_mentioned():
    assert parse_periods("Show monthly revenue for 2026", LAST) == [MonthSpan("2026-01", "2026-12")]


def test_month_not_in_the_data_is_still_parsed():
    # Parsing does not hide missing data; the tool reports it as uncovered.
    assert parse_periods("revenue in March 2030", LAST) == [one("2030-03")]


def test_order_of_appearance_and_no_duplicates():
    assert parse_periods("March compared with February, and March again", LAST) == [one("2026-03"), one("2026-02")]


def test_may_as_a_verb_is_not_a_month():
    assert parse_periods("May I see the top products?", LAST) == []
    assert parse_periods("Top products in May", LAST) == [one("2026-05")]
    assert parse_periods("Top products May 2026", LAST) == [one("2026-05")]


def test_month_words_inside_other_words_are_ignored():
    assert parse_periods("market share by junction and decimal", LAST) == []


def test_no_period_mentioned():
    assert parse_periods("What are the top products?", LAST) == []


@pytest.mark.parametrize(
    ("span", "expected"),
    [
        (one("2026-03"), one("2026-02")),
        (one("2026-01"), one("2025-12")),
        (MonthSpan("2026-04", "2026-06"), MonthSpan("2026-01", "2026-03")),
    ],
)
def test_previous_span_has_the_same_length(span, expected):
    assert previous_span(span) == expected


# --- detect_intent ----------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "intent"),
    [
        ("Why did revenue drop in March?", "change"),
        ("What drove the decline in March?", "change"),
        ("Revenue fell in March, what happened?", "change"),
        ("Why did revenue increase in April?", "change"),
        ("Why did revenue drop in March compared with February?", "change"),  # change beats compare
        ("Compare February and April 2026", "compare"),
        ("March vs April", "compare"),
        ("February versus March", "compare"),
        ("Top 3 products in Q1 2026", "top"),
        ("best-selling products", "top"),
        ("Who are our best sellers?", "top"),
        ("top 5 in March vs April", "top"),  # top beats compare
        ("Show monthly revenue for 2026", "monthly"),
        ("revenue trend this year", "monthly"),
        ("revenue by month", "monthly"),
        ("Revenue by region in Q2 2026", "breakdown"),
        ("Give me a breakdown of revenue", "breakdown"),
        ("revenue per category", "breakdown"),
    ],
)
def test_detect_intent(question, intent):
    assert detect_intent(question) == intent


@pytest.mark.parametrize("question", ["What is the weather?", "hello", "Tell me a joke about databases", ""])
def test_unknown_words_give_no_intent(question):
    assert detect_intent(question) is None


# --- detect_dimension ---------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "dimension"),
    [
        ("Revenue by region in Q2 2026", "region"),
        ("revenue BY REGIONS", "region"),
        ("why did revenue drop by product", "product"),
        ("revenue per category", "category"),
        ("revenue across categories", "category"),
        ("Why did revenue drop in March?", None),
        ("revenue by month", None),
    ],
)
def test_detect_dimension(question, dimension):
    assert detect_dimension(question) == dimension


# --- parse_top_n ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "n"),
    [
        ("Top 3 products in Q1 2026", 3),
        ("top 10 products", 10),
        ("top three products", 3),
        ("Top products", DEFAULT_TOP_N),
        ("best-selling products", DEFAULT_TOP_N),
        ("top 50 products", 50),  # not capped here; the tool's schema rejects it
    ],
)
def test_parse_top_n(question, n):
    assert parse_top_n(question) == n


# --- find_names -----------------------------------------------------------------


def test_find_names_is_case_insensitive_and_ordered_by_appearance():
    assert find_names("how did west and EAST do?", OVERVIEW["regions"]) == ["West", "East"]


def test_find_names_whole_words_only():
    assert find_names("northern stores and the westbound line", OVERVIEW["regions"]) == []


def test_find_names_with_digits_and_ampersand():
    assert find_names("studiobook 16 sales", OVERVIEW["products"]) == ["StudioBook 16"]
    assert find_names("wearables & tablets revenue", OVERVIEW["categories"]) == ["Wearables & Tablets"]


# --- plan_question ----------------------------------------------------------------


def calls(plan):
    return [(call.name, call.arguments) for call in plan.calls]


FEB_MAR = {"period_a_start": "2026-02", "period_a_end": "2026-02", "period_b_start": "2026-03", "period_b_end": "2026-03"}


def test_plan_change_with_two_months_is_chronological():
    plan = plan_question("Why did revenue drop in March compared with February?", OVERVIEW)
    assert calls(plan) == [("get_change_drivers", {**FEB_MAR, "dimension": "product_region"})]


def test_plan_change_with_one_month_uses_the_previous_month():
    plan = plan_question("Why did revenue drop in March?", OVERVIEW)
    assert calls(plan) == [("get_change_drivers", {**FEB_MAR, "dimension": "product_region"})]


def test_plan_change_without_a_period_compares_latest_month_with_previous():
    plan = plan_question("Why did revenue change?", OVERVIEW)
    args = plan.calls[0].arguments
    assert (args["period_a_start"], args["period_b_start"]) == ("2026-05", "2026-06")
    assert plan.note is not None


def test_plan_change_dimension_from_stated_dimension_or_name():
    assert plan_question("Why did revenue drop by category in March?", OVERVIEW).calls[0].arguments["dimension"] == "category"
    plan = plan_question("Why did West revenue drop in March?", OVERVIEW)
    assert plan.calls[0].arguments["dimension"] == "region"
    assert (plan.highlight, plan.highlight_kind) == ("West", "region")


def test_plan_top_for_two_periods_makes_two_calls():
    plan = plan_question("top 5 in March vs April", OVERVIEW)
    assert calls(plan) == [
        ("get_top_products", {"start_month": "2026-03", "end_month": "2026-03", "limit": 5}),
        ("get_top_products", {"start_month": "2026-04", "end_month": "2026-04", "limit": 5}),
    ]


def test_plan_without_a_period_uses_the_full_data_range():
    plan = plan_question("What are the top products?", OVERVIEW)
    assert calls(plan) == [("get_top_products", {"start_month": "2026-01", "end_month": LAST, "limit": 5})]
    assert plan.note is not None


def test_plan_unknown_question_is_none():
    assert plan_question("What is the weather?", OVERVIEW) is None


def test_plan_empty_database_is_none():
    assert plan_question("Top products", {**OVERVIEW, "data_coverage": None}) is None


def test_month_label():
    assert month_label("2026-03") == "March 2026"
