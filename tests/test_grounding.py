import pytest

from app.db.connection import get_connection
from app.llm import RuleBasedProvider
from app.llm.base import FinalAnswer, ProviderResponse
from app.services.grounding import check_grounding
from app.services.orchestrator import run_question
from scripts.seed_data import seed_database

DRIVERS_RESULT = {
    "ok": True,
    "tool": "get_change_drivers",
    "arguments": {"period_a_start": "2026-02", "period_a_end": "2026-02",
                  "period_b_start": "2026-03", "period_b_end": "2026-03", "dimension": "product_region", "limit": 5},
    "data": {
        "period_a": {"start_month": "2026-02", "end_month": "2026-02"},
        "period_b": {"start_month": "2026-03", "end_month": "2026-03"},
        "total_change_cents": -31403200,
        "total_change_display": "-$314,032",
        "total_change_abs_display": "$314,032",
        "pct_change": "-17.2",
        "pct_change_abs": "17.2",
        "rows": [
            {"label": "StudioBook 16 / West", "change_cents": -22491000, "change_display": "-$224,910",
             "share_of_total_change": "71.6"},
        ],
    },
}  # fmt: skip
ARGS = [DRIVERS_RESULT["arguments"]]


def check(answer, results=None, arguments=None):
    return check_grounding(answer, results or [DRIVERS_RESULT], question_arguments=ARGS if arguments is None else arguments)


# --- what passes -----------------------------------------------------------------------


def test_copied_numbers_pass():
    report = check(
        "Revenue fell by $314,032 (17.2%) from February 2026 to March 2026. "
        "StudioBook 16 / West changed by -$224,910, 71.6% of the total change."
    )
    assert report.passed, report.unsupported
    # $314,032, 17.2%, two month labels, -$224,910, 71.6%
    assert report.numbers_checked == 6


def test_label_digits_quarters_years_and_tool_integers_pass():
    results = [
        {"data": {"period": {"start_month": "2026-04", "end_month": "2026-06"}, "rows": [{"label": "UltraBook 14"}]},
         "arguments": {"start_month": "2026-04", "end_month": "2026-06", "limit": 5}},
    ]  # fmt: skip
    report = check("Top 5 products in Q2 2026 (April to June) for 2026: UltraBook 14.", results,
                   arguments=[results[0]["arguments"]])  # fmt: skip
    assert report.passed, report.unsupported


def test_no_numbers_passes_with_zero_checked():
    report = check("Profit isn't in the data, so I can't answer that.", results=[], arguments=[])
    assert report.passed and report.numbers_checked == 0


def test_comma_ending_a_clause_is_not_part_of_the_number():
    assert check("It fell by $314,032, mostly in the West.").passed


# --- what fails ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "unsupported"),
    [
        ("Revenue fell by $999,999.", "$999,999"),  # invented amount
        ("StudioBook 16 / West changed by $224,910.", "$224,910"),  # sign flip: no abs field for this row
        ("Revenue fell by 17.3%.", "17.3%"),  # wrong percentage
        ("Revenue fell by 17%.", "17%"),  # rounded percentage
        ("Revenue fell by $314,032.00.", "$314,032.00"),  # reformatted amount
        ("Revenue fell by $314K.", "$314K"),  # abbreviated amount
        ("Revenue fell by $314032.", "$314032"),  # thousands separator dropped
        ("Revenue fell by -17.2% (down 17.20%).", "17.20%"),  # same value, different string
        ("Across 32 product/region pairs, revenue fell.", "32"),  # computed count, not a tool argument
        ("Revenue fell in March 2031.", "March 2031"),  # month label not in the results
        ("Revenue fell in Q4 2026.", "Q4 2026"),  # quarter not in the results
        ("Revenue fell by ٣١٤ dollars.", "٣١٤"),  # non-ASCII digits are numbers too
    ],
)
def test_unsupported_numbers_fail(answer, unsupported):
    report = check(answer)
    assert not report.passed
    assert unsupported in report.unsupported


def test_sign_flip_fails_without_abs_field_but_passes_with_it():
    # With an abs field present, "$314,032" is allowed: grounding checks numbers, not direction words.
    assert check("Revenue changed by $314,032.").passed
    assert not check("Revenue changed by $224,910.").passed  # this row only has the signed display


def test_integer_from_a_failed_validation_is_not_allowed():
    # Arguments only count once validated; a failed call has arguments None and adds nothing.
    assert not check("Top 50 products.", arguments=[]).passed


# --- the orchestrator withholds ungrounded answers ---------------------------------------


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("grounding") / "grounding.db"
    seed_database(db_path, seed=42, start="2026-01", months=6)
    connection = get_connection(db_path, read_only=True)
    yield connection
    connection.close()


class HallucinatingProvider(RuleBasedProvider):
    """The real planner, but its final answer gets one invented number appended."""

    def next_turn(self, messages, tools):
        response = super().next_turn(messages, tools)
        if isinstance(response.turn, FinalAnswer):
            return ProviderResponse(FinalAnswer(response.turn.text + " Revenue will grow by $999,999."))
        return response


def test_hallucinated_number_is_withheld(conn, log_lines):
    result = run_question("Why did revenue drop in March compared with February?", conn, HallucinatingProvider())
    assert result.status == "grounding_failed"
    assert result.answer is None
    assert result.message
    assert result.grounding.passed is False
    assert result.grounding.unsupported == ["$999,999"]
    assert len(result.tool_calls) == 2  # the tools still ran; only the answer is withheld
    failed = [line for line in log_lines() if line.get("event") == "grounding_failed"]
    assert len(failed) == 1 and failed[0]["unsupported"] == ["$999,999"]
    completed = next(line for line in log_lines() if line.get("event") == "question_completed")
    assert completed["status"] == "grounding_failed" and completed["grounding_passed"] is False


def test_grounded_answer_reports_numbers_checked(conn):
    result = run_question("Why did revenue drop in March compared with February?", conn, RuleBasedProvider())
    assert result.status == "answered"
    assert result.grounding.passed is True
    assert result.grounding.numbers_checked == 12
