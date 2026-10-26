"""The rule-based provider + orchestrator + real tools, on the seeded test database."""

import re

import pytest

from app.db.connection import get_connection
from app.llm import RuleBasedProvider
from app.llm.base import ToolResultMessage
from app.services import analytics
from app.services.analytics import Period
from app.services.grounding import check_grounding
from app.services.orchestrator import run_question
from scripts.seed_data import seed_database

FEB_MAR = {"period_a_start": "2026-02", "period_a_end": "2026-02", "period_b_start": "2026-03", "period_b_end": "2026-03"}


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("e2e") / "e2e.db"
    seed_database(db_path, seed=42, start="2026-01", months=6)
    connection = get_connection(db_path, read_only=True)
    yield connection
    connection.close()


class RecordingProvider(RuleBasedProvider):
    """The real provider, also keeping the last messages so tests can see this run's tool results."""

    def next_turn(self, messages, tools):
        self.messages = list(messages)
        return super().next_turn(messages, tools)


def ask(conn, question):
    provider = RecordingProvider()
    result = run_question(question, conn, provider)
    results = [m.result for m in provider.messages if isinstance(m, ToolResultMessage)]
    return result, results


def calls(result):
    return [(t.tool, t.arguments) for t in result.tool_calls]


# --- the three answers from the previous review, in the new wording ------------------------


def test_why_did_revenue_drop_in_march_compared_with_february(conn):
    result, _ = ask(conn, "Why did revenue drop in March compared with February?")
    assert result.status == "answered"
    assert calls(result) == [
        ("get_data_overview", {}),
        ("get_change_drivers", {**FEB_MAR, "dimension": "product_region", "limit": 5}),
    ]
    assert result.answer == (
        "Revenue fell by $314,032 (17.2%), from $1,825,822 in February 2026 to $1,511,790 in March 2026."
        " The largest driver was StudioBook 16 / West, which fell by $224,910 from $304,878 to $79,968,"
        " 71.6% of the total change."
        " No other product/region pair accounts for more than 2.8% (next largest: Phone Max / East, -$8,792)."
    )


def test_top_5_in_march_vs_april_is_two_lists_on_separate_lines(conn):
    result, _ = ask(conn, "Top 5 in March vs April")
    assert result.status == "answered"
    assert calls(result)[1:] == [
        ("get_top_products", {"start_month": "2026-03", "end_month": "2026-03", "limit": 5}),
        ("get_top_products", {"start_month": "2026-04", "end_month": "2026-04", "limit": 5}),
    ]
    assert result.answer == (
        "Top 5 products by revenue:\n"
        "March 2026: Phone Max ($283,542); StudioBook 16 ($279,888); UltraBook 14 ($269,775);"
        " Phone Lite ($214,071); Tablet 11 ($135,374).\n"
        "April 2026: StudioBook 16 ($512,295); Phone Max ($302,225); UltraBook 14 ($290,158);"
        " Phone Lite ($230,039); Tablet 11 ($141,963)."
    )


def test_revenue_by_region_in_q2_names_the_quarter(conn):
    result, _ = ask(conn, "Revenue by region in Q2 2026")
    assert calls(result)[1] == (
        "get_revenue_breakdown",
        {"start_month": "2026-04", "end_month": "2026-06", "dimension": "region"},
    )
    assert result.answer == (
        "Revenue by region for Q2 2026 (April to June): West $2,021,557; East $1,357,732; North $1,245,938; South $937,128."
    )


# --- other planned questions ---------------------------------------------------------------


def test_why_did_revenue_drop_in_march_uses_the_previous_month(conn):
    result, _ = ask(conn, "Why did revenue drop in March?")
    assert calls(result) == [
        ("get_data_overview", {}),
        ("get_change_drivers", {**FEB_MAR, "dimension": "product_region", "limit": 5}),
    ]
    assert result.answer.startswith("Revenue fell by $314,032 (17.2%)")


def test_compare_february_and_april(conn):
    result, _ = ask(conn, "Compare February and April 2026")
    assert calls(result)[1] == (
        "compare_periods",
        {"period_a_start": "2026-02", "period_a_end": "2026-02", "period_b_start": "2026-04", "period_b_end": "2026-04"},
    )
    assert "February 2026" in result.answer and "April 2026" in result.answer


def test_top_3_products_in_q1(conn):
    result, results = ask(conn, "Top 3 products in Q1 2026")
    assert calls(result)[1] == ("get_top_products", {"start_month": "2026-01", "end_month": "2026-03", "limit": 3})
    assert result.answer.startswith("Top 3 products by revenue in Q1 2026 (January to March): ")
    for row in results[1]["data"]["rows"]:
        assert f"{row['label']} ({row['revenue_display']})" in result.answer


def test_top_50_is_capped_at_20_and_answered(conn):
    result, _ = ask(conn, "Top 50 products in Q1 2026")
    assert result.status == "answered"
    assert result.tool_calls[1].arguments["limit"] == 20
    assert result.tool_calls[1].ok
    assert "showing up to the maximum of 20" in result.answer
    # 50 is not in any tool result, so it must not be quoted (as a whole number: "$850,091" is fine).
    assert not re.search(r"(?<![0-9,$])50(?![0-9,])", result.answer)


def test_monthly_revenue_for_2026_mentions_missing_months(conn):
    result, _ = ask(conn, "Show monthly revenue for 2026")
    assert calls(result)[1] == ("get_revenue_by_month", {"start_month": "2026-01", "end_month": "2026-12"})
    assert result.answer.startswith("Monthly revenue for 2026 (January to December): January 2026 $1,780,189;")
    assert "There is no data for July 2026" in result.answer
    assert "the data covers January 2026 to June 2026" in result.answer


def test_plain_lookup_with_a_lowercase_known_region(conn):
    result, _ = ask(conn, "revenue in the west in march")
    assert result.status == "answered"
    assert calls(result)[1] == (
        "get_revenue_breakdown",
        {"start_month": "2026-03", "end_month": "2026-03", "dimension": "region"},
    )
    assert "This shows all regions with West highlighted; it is not filtered to West." in result.answer


def test_single_month_lookup(conn):
    result, _ = ask(conn, "Revenue in March 2026")
    assert result.answer == "Revenue in March 2026 was $1,511,790."


def test_change_question_without_period_compares_latest_month_with_previous(conn):
    result, _ = ask(conn, "Why did revenue change?")
    args = result.tool_calls[1].arguments
    assert (args["period_a_start"], args["period_b_start"]) == ("2026-05", "2026-06")
    assert "latest month" in result.answer


def test_highlighted_top_driver_is_not_repeated(conn):
    result, _ = ask(conn, "Why did West revenue drop in March?")
    assert result.tool_calls[1].arguments["dimension"] == "region"
    assert "This shows all regions with West highlighted; it is not filtered to West." in result.answer
    assert result.answer.count("$243,979") == 1  # West's change appears once, in the driver sentence


def test_named_comparison_answers_with_the_items_own_row_not_company_totals(conn):
    # compare_periods would return company totals: a confident wrong answer for a named product.
    result, results = ask(conn, "How did StudioBook 16 do in March vs February?")
    assert calls(result)[1] == (
        "get_change_drivers",
        {**FEB_MAR, "dimension": "product", "limit": 20},
    )
    row = next(r for r in results[1]["data"]["rows"] if r["label"] == "StudioBook 16")
    assert result.answer == (
        f"StudioBook 16 revenue fell by {row['change_abs_display']}, from {row['revenue_a_display']} in February 2026"
        f" to {row['revenue_b_display']} in March 2026. That is {row['share_of_total_change']}% of the total change"
        f" in revenue ({results[1]['data']['total_change_display']})."
    )
    assert "$1,825,822" not in result.answer  # February's company total is not presented as the product's


def test_monthly_question_naming_a_region_says_figures_are_not_filtered(conn):
    result, _ = ask(conn, "Show monthly revenue for West in Q1 2026")
    assert calls(result)[1][0] == "get_revenue_by_month"
    assert "I can't filter them to West" in result.answer


def test_month_outside_the_data_is_reported_not_guessed(conn):
    result, _ = ask(conn, "Top products in December")
    assert result.status == "answered"
    assert "There is no data for December 2025" in result.answer


def test_empty_database(empty_conn):
    result = run_question("Top products", empty_conn, RuleBasedProvider())
    assert result.status == "answered"
    assert "no sales data" in result.answer


# --- false premise: the direction word comes from the data, never from the question ----------


def test_false_premise_drop_when_revenue_rose(conn):
    # Check what the data says first, so the test asserts the data, not an assumption.
    may, june = Period.of_month("2026-05"), Period.of_month("2026-06")
    comparison = analytics.compare_periods(conn, may, june)
    assert comparison.change_cents > 0, "seed data: revenue rose from May to June"

    result, _ = ask(conn, "Why did revenue drop from May to June?")
    assert result.status == "answered"
    assert result.answer.startswith("Revenue didn't drop: it rose by $34,289 (1.9%) from May 2026 to June 2026")
    assert "fell" not in result.answer.split(".")[0]


def test_false_premise_increase_when_revenue_fell(conn):
    result, _ = ask(conn, "Why did revenue increase in March?")
    assert result.answer.startswith("Revenue didn't rise: it fell by $314,032 (17.2%) from February 2026 to March 2026")


def test_correct_premise_is_not_contradicted(conn):
    result, _ = ask(conn, "Why did revenue increase from May to June?")
    assert result.answer.startswith("Revenue rose by $34,289 (1.9%), from $1,852,189 in May 2026")


# --- determinism and grounding over many questions -----------------------------------------

QUESTIONS = [
    "Why did revenue drop in March compared with February?",
    "Why did revenue drop in March?",
    "Compare February and April 2026",
    "Revenue by region in Q2 2026",
    "Top 3 products in Q1 2026",
    "Top 50 products in Q1 2026",
    "Show monthly revenue for 2026",
    "Top 5 in March vs April",
    "Why did West revenue drop in March?",
    "Why did revenue change?",
    "Why did revenue drop from May to June?",
    "Why did revenue drop by region in Q1 vs Q2?",
    "Top products in December",
    "How did StudioBook 16 do in March vs February?",
    "revenue in the west in march",
    "Revenue in March 2026",
    "Revenue by category in 2026",
]


@pytest.mark.parametrize("question", QUESTIONS)
def test_same_question_twice_gives_identical_answers(conn, question):
    first, _ = ask(conn, question)
    second, _ = ask(conn, question)
    assert first.answer == second.answer
    assert calls(first) == calls(second)


@pytest.mark.parametrize("question", QUESTIONS)
def test_every_rule_based_answer_passes_grounding(conn, question):
    result, results = ask(conn, question)
    assert result.status == "answered"
    assert result.grounding.passed and result.grounding.unsupported == []
    assert result.grounding.numbers_checked > 0
    # Re-run the check directly on the same inputs: same verdict.
    arguments = [t.arguments for t in result.tool_calls if t.arguments]
    assert check_grounding(result.answer, results, question_arguments=arguments).passed


def test_rule_based_usage_is_zero(conn):
    result, _ = ask(conn, "Top 3 products in Q1 2026")
    assert result.usage.model_dump() == {"input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0}
