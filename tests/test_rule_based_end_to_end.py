"""The rule-based provider + orchestrator + real tools, on the seeded test database."""

import re

import pytest

from app.db.connection import get_connection
from app.llm import RuleBasedProvider
from app.llm.base import ToolResultMessage
from app.llm.question_parser import MONTH_NAMES
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


# --- grounding check (simple version; the real grounding check comes in task 3) -----------

_MONEY = re.compile(r"-?\$[0-9][0-9,]*\.[0-9]{2}")
_PERCENT = re.compile(r"(-?[0-9]+(?:\.[0-9]+)?)%")
_MONTH_LABEL = re.compile(r"\b(" + "|".join(MONTH_NAMES) + r") ([0-9]{4})\b")
_PERCENT_KEYS = {"pct_change", "share_of_total_change"}


def _walk(obj, key=None):
    """Yield (key, value) for every leaf in a nested structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, k)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk(item, key)
    else:
        yield key, obj


def assert_numbers_grounded(answer: str, results: list[dict]) -> None:
    """Every number in the answer must be copied from this run's tool results.

    - money ($...) must equal a *_display value exactly
    - percentages must equal a percentage string (pct_change, share_of_total_change)
    - "March 2026" month labels must name a YYYY-MM month that appears in the results
    - names with digits ("StudioBook 16") are removed first: they are labels, not figures
    Anything with a digit left over fails the check.
    """
    leaves = list(_walk(results))
    displays = {v for k, v in leaves if isinstance(k, str) and k.endswith("_display")}
    percents = {v for k, v in leaves if k in _PERCENT_KEYS and v is not None}
    months = {v for _, v in leaves if isinstance(v, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}", v)}
    labels = {v for k, v in leaves if k in ("label", "products", "regions", "categories") and isinstance(v, str)}

    text = answer
    for label in sorted(labels, key=len, reverse=True):
        text = text.replace(label, "<label>")
    for money in _MONEY.findall(text):
        assert money in displays, f"{money} is not a *_display value from the tool results"
    text = _MONEY.sub("<money>", text)
    for percent in _PERCENT.findall(text):
        assert percent in percents, f"{percent}% is not a percentage from the tool results"
    text = _PERCENT.sub("<pct>", text)
    for name, year in _MONTH_LABEL.findall(text):
        month = f"{year}-{MONTH_NAMES.index(name) + 1:02d}"
        assert month in months, f"{name} {year} does not appear in the tool results"
    text = _MONTH_LABEL.sub("<month>", text)
    assert not re.search(r"[0-9]", text), f"ungrounded number left in: {text!r}"


def test_grounding_check_catches_an_invented_number():
    results = [{"data": {"change_display": "-$1.00", "share_of_total_change": "50.0"}}]
    assert_numbers_grounded("Down -$1.00 (50.0%).", results)
    for bad in ("Down -$2.00.", "Down -$1.00 (51.0%).", "Down 3 units.", "In March 2026."):
        with pytest.raises(AssertionError):
            assert_numbers_grounded(bad, results)


# --- the required questions ----------------------------------------------------------------

QUESTIONS = [
    "Why did revenue drop in March compared with February?",
    "Why did revenue drop in March?",
    "Compare February and April 2026",
    "Revenue by region in Q2 2026",
    "Top 3 products in Q1 2026",
    "Show monthly revenue for 2026",
    "top 5 in March vs April",
    "Why did West revenue drop in March?",
    "Why did revenue change?",
    "Top products in December",
    "How did StudioBook 16 do in March vs February?",
]


def test_why_did_revenue_drop_in_march_compared_with_february(conn):
    result, _ = ask(conn, "Why did revenue drop in March compared with February?")
    assert result.status == "answered"
    assert calls(result) == [
        ("get_data_overview", {}),
        ("get_change_drivers", {**FEB_MAR, "dimension": "product_region", "limit": 5}),
    ]
    for expected in ("StudioBook 16 / West", "-$224,910.00", "71.6"):
        assert expected in result.answer


def test_why_did_revenue_drop_in_march_uses_the_previous_month(conn):
    result, _ = ask(conn, "Why did revenue drop in March?")
    assert result.status == "answered"
    assert calls(result) == [
        ("get_data_overview", {}),
        ("get_change_drivers", {**FEB_MAR, "dimension": "product_region", "limit": 5}),
    ]
    assert "StudioBook 16 / West" in result.answer


def test_compare_february_and_april(conn):
    result, _ = ask(conn, "Compare February and April 2026")
    assert calls(result)[1] == (
        "compare_periods",
        {"period_a_start": "2026-02", "period_a_end": "2026-02", "period_b_start": "2026-04", "period_b_end": "2026-04"},
    )
    assert "February 2026" in result.answer and "April 2026" in result.answer


def test_revenue_by_region_in_q2(conn):
    result, _ = ask(conn, "Revenue by region in Q2 2026")
    assert calls(result)[1] == (
        "get_revenue_breakdown",
        {"start_month": "2026-04", "end_month": "2026-06", "dimension": "region"},
    )
    assert "West" in result.answer


def test_top_3_products_in_q1(conn):
    result, results = ask(conn, "Top 3 products in Q1 2026")
    assert calls(result)[1] == ("get_top_products", {"start_month": "2026-01", "end_month": "2026-03", "limit": 3})
    for row in results[1]["data"]["rows"]:
        assert row["label"] in result.answer


def test_monthly_revenue_for_2026_mentions_missing_months(conn):
    result, _ = ask(conn, "Show monthly revenue for 2026")
    assert calls(result)[1] == ("get_revenue_by_month", {"start_month": "2026-01", "end_month": "2026-12"})
    assert "There is no data for July 2026" in result.answer
    assert "the data covers January 2026 to June 2026" in result.answer


def test_top_5_in_march_vs_april_gets_top_products_for_both_months(conn):
    result, results = ask(conn, "top 5 in March vs April")
    assert result.status == "answered"
    assert calls(result)[1:] == [
        ("get_top_products", {"start_month": "2026-03", "end_month": "2026-03", "limit": 5}),
        ("get_top_products", {"start_month": "2026-04", "end_month": "2026-04", "limit": 5}),
    ]
    assert "in March 2026:" in result.answer and "in April 2026:" in result.answer
    # Each month's best seller is quoted with its own figure.
    for tool_result in results[1:]:
        top = tool_result["data"]["rows"][0]
        assert f"{top['label']} ({top['revenue_display']})" in result.answer


def test_change_question_without_period_compares_latest_month_with_previous(conn):
    result, _ = ask(conn, "Why did revenue change?")
    args = result.tool_calls[1].arguments
    assert (args["period_a_start"], args["period_b_start"]) == ("2026-05", "2026-06")
    assert "latest month" in result.answer


def test_name_steers_dimension_and_answer_says_it_is_highlighted_not_filtered(conn):
    result, _ = ask(conn, "Why did West revenue drop in March?")
    assert result.tool_calls[1].arguments["dimension"] == "region"
    assert "This shows all regions with West highlighted; it is not filtered to West." in result.answer


def test_name_that_cannot_be_highlighted_says_figures_are_not_filtered(conn):
    result, _ = ask(conn, "How did StudioBook 16 do in March vs February?")
    assert calls(result)[1][0] == "compare_periods"
    assert "I can't filter them to StudioBook 16" in result.answer


def test_month_outside_the_data_is_reported_not_guessed(conn):
    result, _ = ask(conn, "Top products in December")
    assert result.status == "answered"
    assert "There is no data for December 2025" in result.answer


def test_unsupported_question(conn):
    result, _ = ask(conn, "What is the weather?")
    assert result.status == "answered"
    assert calls(result) == [("get_data_overview", {})]
    assert "can't answer that question yet" in result.answer


def test_tool_error_gives_an_answer_with_the_code_and_no_numbers(conn):
    result, _ = ask(conn, "Top 50 products")  # the schema caps limit at 20
    assert result.status == "answered"
    assert result.tool_calls[1].error_code == "invalid_arguments"
    assert '"invalid_arguments"' in result.answer
    assert not re.search(r"[0-9]", result.answer)


def test_empty_database(empty_conn):
    result = run_question("Top products", empty_conn, RuleBasedProvider())
    assert result.status == "answered"
    assert "no sales data" in result.answer


@pytest.mark.parametrize("question", QUESTIONS)
def test_same_question_twice_gives_identical_answers(conn, question):
    first, _ = ask(conn, question)
    second, _ = ask(conn, question)
    assert first.answer == second.answer
    assert calls(first) == calls(second)


@pytest.mark.parametrize("question", QUESTIONS)
def test_every_number_in_every_answer_comes_from_the_tool_results(conn, question):
    result, results = ask(conn, question)
    assert result.status == "answered"
    assert_numbers_grounded(result.answer, results)


def test_rule_based_usage_is_zero(conn):
    result, _ = ask(conn, "Top 3 products in Q1 2026")
    assert result.usage.model_dump() == {"input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0.0}
