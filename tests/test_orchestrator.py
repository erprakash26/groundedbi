import pytest

from app.llm.base import (
    Clarify,
    FinalAnswer,
    Pricing,
    ProviderResponse,
    ToolCall,
    ToolCalls,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from app.observability.context import request_id_var
from app.services import orchestrator
from app.services.orchestrator import MAX_QUESTION_LENGTH, run_question

OVERVIEW = ToolCall("c1", "get_data_overview", {})
TOP_3 = ToolCall("c2", "get_top_products", {"start_month": "2026-01", "end_month": "2026-03", "limit": 3})


class FakeClock:
    """Time only moves when a test moves it."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class ScriptedProvider:
    """Test double: returns predetermined turns in order, whatever the messages say.

    A script entry is a Turn, an Exception to raise, or a callable returning either
    (used to move a FakeClock while the "provider is thinking").
    """

    name = "scripted"

    def __init__(self, script, *, usage=Usage(), pricing=Pricing()):
        self.script = list(script)
        self.usage = usage
        self.pricing = pricing
        self.seen: list[tuple] = []  # the messages passed on each turn

    def next_turn(self, messages, tools):
        self.seen.append(tuple(messages))
        entry = self.script.pop(0)
        if callable(entry):
            entry = entry()
        if isinstance(entry, Exception):
            raise entry
        return ProviderResponse(entry, self.usage)


def ask(conn, provider, question="Top 3 products in Q1 2026?", **kwargs):
    return run_question(question, conn, provider, **kwargs)


def tool_results(messages) -> list[dict]:
    return [m.result for m in messages if isinstance(m, ToolResultMessage)]


# --- happy path -----------------------------------------------------------------


def test_happy_path(seeded_conn):
    provider = ScriptedProvider([ToolCalls((OVERVIEW,)), ToolCalls((TOP_3,)), FinalAnswer("Done.")])
    result = ask(seeded_conn, provider)
    assert result.status == "answered"
    assert result.answer == "Done."
    assert result.message is None and result.clarifying_question is None
    assert result.steps == 3
    assert [t.tool for t in result.tool_calls] == ["get_data_overview", "get_top_products"]
    assert all(t.ok for t in result.tool_calls)
    assert result.tool_calls[1].arguments == TOP_3.arguments
    # The provider saw the question first and then each tool result, matched by call id.
    last_seen = provider.seen[-1]
    assert last_seen[0] == UserMessage("Top 3 products in Q1 2026?")
    assert [m.call_id for m in last_seen if isinstance(m, ToolResultMessage)] == ["c1", "c2"]
    assert result.usage.estimated_cost_usd == 0


def test_question_is_stripped_before_use(seeded_conn):
    provider = ScriptedProvider([FinalAnswer("ok")])
    ask(seeded_conn, provider, question="  Top products?  \n")
    assert provider.seen[0][0] == UserMessage("Top products?")


def test_clarify_turn(seeded_conn):
    result = ask(seeded_conn, ScriptedProvider([Clarify("Which year do you mean?")]))
    assert result.status == "clarification_needed"
    assert result.clarifying_question == "Which year do you mean?"
    assert result.answer is None


# --- tool errors are fed back -----------------------------------------------------


def test_tool_error_is_sent_back_and_the_loop_continues(seeded_conn):
    bad = ToolCall("c1", "get_top_products", {"start_month": "2026-01", "end_month": "2026-03", "limit": 0})
    provider = ScriptedProvider([ToolCalls((bad,)), ToolCalls((TOP_3,)), FinalAnswer("Recovered.")])
    result = ask(seeded_conn, provider)
    assert result.status == "answered"
    assert [(t.ok, t.error_code) for t in result.tool_calls] == [(False, "invalid_arguments"), (True, None)]
    assert result.tool_calls[0].arguments is None
    # The second turn saw the failed result, with its error code.
    fed_back = tool_results(provider.seen[1])
    assert fed_back[0]["ok"] is False and fed_back[0]["error"]["code"] == "invalid_arguments"


def test_unknown_tool_from_provider_is_fed_back(seeded_conn):
    provider = ScriptedProvider([ToolCalls((ToolCall("c1", "drop_tables", {}),)), FinalAnswer("Sorry.")])
    result = ask(seeded_conn, provider)
    assert result.status == "answered"
    assert (result.tool_calls[0].tool, result.tool_calls[0].error_code) == (None, "unknown_tool")


# --- limits -------------------------------------------------------------------------


def test_batch_over_max_tool_calls_is_refused_without_running_any(seeded_conn):
    batch = ToolCalls(tuple(ToolCall(f"c{i}", "get_data_overview", {}) for i in range(3)))
    provider = ScriptedProvider([batch, FinalAnswer("never reached")])
    result = ask(seeded_conn, provider, max_tool_calls=2)
    assert result.status == "tool_call_limit_exceeded"
    assert result.tool_calls == []  # nothing from the refused batch ran
    assert result.answer is None and result.message


def test_max_tool_calls_reached_across_turns(seeded_conn):
    provider = ScriptedProvider([ToolCalls((OVERVIEW,)), ToolCalls((TOP_3,)), ToolCalls((OVERVIEW,))])
    result = ask(seeded_conn, provider, max_tool_calls=2)
    assert result.status == "tool_call_limit_exceeded"
    assert len(result.tool_calls) == 2
    assert result.answer is None


def test_exactly_max_tool_calls_is_allowed(seeded_conn):
    provider = ScriptedProvider([ToolCalls((OVERVIEW, TOP_3)), FinalAnswer("ok")])
    assert ask(seeded_conn, provider, max_tool_calls=2).status == "answered"


def test_max_steps(seeded_conn):
    provider = ScriptedProvider([ToolCalls((ToolCall(f"c{i}", "get_data_overview", {}),)) for i in range(10)])
    result = ask(seeded_conn, provider, max_steps=3, max_tool_calls=10)
    assert result.status == "limit_exceeded"
    assert result.steps == 3
    assert len(result.tool_calls) == 3
    assert result.answer is None


def test_deadline_already_passed_before_the_first_turn(seeded_conn):
    provider = ScriptedProvider([FinalAnswer("never reached")])
    result = ask(seeded_conn, provider, deadline_seconds=0.0)
    assert result.status == "limit_exceeded"
    assert result.steps == 0 and provider.seen == []


def test_deadline_expires_mid_run_and_the_late_turn_is_discarded(seeded_conn):
    clock = FakeClock()

    def slow_final_answer():
        clock.now += 11.0  # the provider "thinks" past the 10 s deadline
        return FinalAnswer("too late")

    provider = ScriptedProvider([ToolCalls((OVERVIEW,)), slow_final_answer])
    result = ask(seeded_conn, provider, deadline_seconds=10.0, clock=clock)
    assert result.status == "limit_exceeded"
    assert result.answer is None  # the late answer is not used
    assert result.steps == 2
    assert [t.tool for t in result.tool_calls] == ["get_data_overview"]  # work before the deadline is kept
    assert result.duration_ms == 11000.0


def test_tool_time_budget_never_exceeds_the_time_left(seeded_conn, monkeypatch):
    clock = FakeClock()
    budgets = []
    real_execute = orchestrator.execute_tool

    def spy(conn, name, arguments, *, time_budget_seconds):
        budgets.append(time_budget_seconds)
        return real_execute(conn, name, arguments, time_budget_seconds=time_budget_seconds)

    def after_9_5_seconds():
        clock.now = 9.5
        return ToolCalls((TOP_3,))

    monkeypatch.setattr(orchestrator, "execute_tool", spy)
    provider = ScriptedProvider([ToolCalls((OVERVIEW,)), after_9_5_seconds, FinalAnswer("ok")])
    ask(seeded_conn, provider, deadline_seconds=10.0, clock=clock)
    assert budgets == [orchestrator.TOOL_TIME_BUDGET_SECONDS, 0.5]


# --- provider failures ---------------------------------------------------------------


def test_provider_raising_is_provider_error_with_traceback_logged(seeded_conn, log_lines):
    provider = ScriptedProvider([ToolCalls((OVERVIEW,)), RuntimeError("secret provider detail")])
    result = ask(seeded_conn, provider)
    assert result.status == "provider_error"
    assert result.answer is None
    assert "secret" not in result.model_dump_json()
    assert len(result.tool_calls) == 1  # the work done before the failure is still reported
    failed = [line for line in log_lines() if line.get("event") == "provider_failed"]
    assert len(failed) == 1 and "RuntimeError: secret provider detail" in failed[0]["traceback"]


@pytest.mark.parametrize("bad_turn", [None, "a string", ToolCalls(()), FinalAnswer("   "), Clarify("")])
def test_invalid_turn_is_provider_error(seeded_conn, bad_turn):
    assert ask(seeded_conn, ScriptedProvider([bad_turn])).status == "provider_error"


def test_provider_returning_a_non_response_is_provider_error(seeded_conn):
    class Broken:
        name = "broken"
        pricing = Pricing()

        def next_turn(self, messages, tools):
            return FinalAnswer("not wrapped in a ProviderResponse")

    assert ask(seeded_conn, Broken()).status == "provider_error"


def test_bug_in_the_loop_is_error_status(seeded_conn, monkeypatch, log_lines):
    def broken_execute(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(orchestrator, "execute_tool", broken_execute)
    result = ask(seeded_conn, ScriptedProvider([ToolCalls((OVERVIEW,))]))
    assert result.status == "error"
    assert result.message == "An internal error occurred while answering the question."
    assert any(line.get("event") == "orchestrator_failed" for line in log_lines())


# --- question validation ----------------------------------------------------------------


@pytest.mark.parametrize("question", ["", "   ", "\n\t", "x" * (MAX_QUESTION_LENGTH + 1), None, 42])
def test_invalid_questions_raise_before_the_loop(seeded_conn, question, log_lines):
    provider = ScriptedProvider([FinalAnswer("never reached")])
    with pytest.raises(ValueError):
        run_question(question, seeded_conn, provider)
    assert provider.seen == []
    assert log_lines() == []  # nothing is logged for a rejected question


def test_question_of_exactly_max_length_is_accepted(seeded_conn):
    assert ask(seeded_conn, ScriptedProvider([FinalAnswer("ok")]), question="x" * MAX_QUESTION_LENGTH).status == "answered"


# --- usage and logging ------------------------------------------------------------------


def test_usage_and_cost_are_summed_over_turns(seeded_conn):
    provider = ScriptedProvider(
        [ToolCalls((OVERVIEW,)), FinalAnswer("ok")],
        usage=Usage(input_tokens=1000, output_tokens=200),
        pricing=Pricing(input_usd_per_million=3.0, output_usd_per_million=15.0),
    )
    usage = ask(seeded_conn, provider).usage
    assert (usage.input_tokens, usage.output_tokens) == (2000, 400)
    assert usage.estimated_cost_usd == pytest.approx((2000 * 3.0 + 400 * 15.0) / 1_000_000)


def test_log_lines_are_json_with_request_id(seeded_conn, log_lines):
    long_question = "Top products? " + "y" * 300
    token = request_id_var.set("req-orch-1")
    try:
        ask(seeded_conn, ScriptedProvider([ToolCalls((OVERVIEW,)), FinalAnswer("ok")]), question=long_question)
    finally:
        request_id_var.reset(token)
    lines = log_lines()  # each line already parsed with json.loads
    received = next(line for line in lines if line.get("event") == "question_received")
    completed = next(line for line in lines if line.get("event") == "question_completed")
    assert received["request_id"] == completed["request_id"] == "req-orch-1"
    assert received["question_length"] == len(long_question)
    assert received["question"] == long_question[:200]
    assert completed["status"] == "answered"
    assert completed["steps"] == 2 and completed["tool_call_count"] == 1
    assert (completed["input_tokens"], completed["output_tokens"], completed["estimated_cost_usd"]) == (0, 0, 0)
    assert isinstance(completed["duration_ms"], float)
    # Tool executions inside the run carry the same request ID.
    assert all(line["request_id"] == "req-orch-1" for line in lines if line.get("event") == "tool_executed")
