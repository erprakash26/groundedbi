"""run_question: the loop between a provider (the planner) and the tools. Knows nothing about FastAPI.

  question -> provider.next_turn -> ToolCalls? execute each via execute_tool, append results, repeat
                                 -> FinalAnswer / Clarify? stop

Every FinalAnswer goes through the grounding check (app/services/grounding.py) before it is
returned: an answer with a number that cannot be traced to this run's tool output is withheld.

Every limit is checked BEFORE acting, so a stopped run never half-executes a step:
  - no provider turn is started once max_steps turns have been made or the deadline has passed;
  - a turn that comes back after the deadline is discarded;
  - a batch of tool calls that would take the total past max_tool_calls is refused as a whole.
A stopped run never gets a made-up answer: answer is None and message says why.

Statuses:
  answered                  the provider gave a final answer and it passed the grounding check
  out_of_scope              the provider politely refused: the data can never answer this question
  clarification_needed      the provider asked the user a question back
  grounding_failed          the final answer had numbers not found in the tool output; it is withheld
  limit_exceeded            max_steps or the deadline was reached
  tool_call_limit_exceeded  the provider asked for a batch of tool calls that would exceed max_tool_calls
  provider_error            the provider raised, or returned something that is not a valid turn
  error                     an unexpected bug in this loop
"""

import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from app.llm.base import (
    AssistantToolCalls,
    Clarify,
    FinalAnswer,
    Message,
    Provider,
    ProviderResponse,
    ToolCalls,
    ToolResultMessage,
    Usage,
    UserMessage,
    estimate_cost_usd,
)
from app.services.grounding import check_grounding
from app.tools import execute_tool, tool_definitions

logger = logging.getLogger("app.orchestrator")

MAX_QUESTION_LENGTH = 500
LOGGED_QUESTION_LENGTH = 200
# No single tool may run longer than this, and never longer than the time left before the deadline.
TOOL_TIME_BUDGET_SECONDS = 2.0

Status = Literal[
    "answered",
    "out_of_scope",
    "clarification_needed",
    "grounding_failed",
    "limit_exceeded",
    "tool_call_limit_exceeded",
    "provider_error",
    "error",
]


class ToolCallTrace(BaseModel):
    call_id: str
    tool: str | None  # None if the provider named an unknown tool (never echo unknown input)
    arguments: dict | None  # normalized; None if validation failed
    ok: bool
    error_code: str | None
    duration_ms: float


class UsageOut(BaseModel):
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float


class GroundingOut(BaseModel):
    passed: bool
    numbers_checked: int
    unsupported: list[str]  # numbers in the withheld answer that no tool result contains


class AskResult(BaseModel):
    status: Status
    answer: str | None
    clarifying_question: str | None
    message: str | None  # why there is no answer (limits, errors, failed grounding); None when answered
    grounding: GroundingOut | None  # set whenever the provider gave a final answer
    tool_calls: list[ToolCallTrace]
    steps: int  # provider turns made
    usage: UsageOut
    duration_ms: float


@dataclass
class _Run:
    """Mutable state of one run, so partial progress is reported even when the loop stops early."""

    steps: int = 0
    traces: list[ToolCallTrace] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    grounding: GroundingOut | None = None


@dataclass(frozen=True)
class _Outcome:
    status: Status
    answer: str | None = None
    clarifying_question: str | None = None
    message: str | None = None


def validate_question(question: object) -> str:
    """The stripped question. Raises ValueError for empty, whitespace-only or over-long input."""
    if not isinstance(question, str):
        raise ValueError("question must be a string")
    stripped = question.strip()
    if not stripped:
        raise ValueError("question must not be empty")
    if len(stripped) > MAX_QUESTION_LENGTH:
        raise ValueError(f"question must be at most {MAX_QUESTION_LENGTH} characters")
    return stripped


def run_question(
    question: str,
    conn: sqlite3.Connection,
    provider: Provider,
    *,
    max_tool_calls: int = 4,
    max_steps: int = 6,
    deadline_seconds: float = 10.0,
    clock: Callable[[], float] = time.perf_counter,  # injectable so tests can move time deterministically
) -> AskResult:
    question = validate_question(question)  # raises before anything runs or is logged
    started = clock()
    logger.info(
        "question_received",
        extra={
            "event": "question_received",
            "question_length": len(question),
            "question": question[:LOGGED_QUESTION_LENGTH],
            "provider": provider.name,
        },
    )
    run = _Run()
    try:
        outcome = _loop(question, conn, provider, run, started + deadline_seconds, clock,
                        max_tool_calls=max_tool_calls, max_steps=max_steps)
    except Exception as exc:
        logger.error(
            "orchestrator_failed", exc_info=(type(exc), exc, exc.__traceback__), extra={"event": "orchestrator_failed"}
        )
        outcome = _Outcome("error", message="An internal error occurred while answering the question.")

    cost = estimate_cost_usd(run.usage, provider.pricing)
    result = AskResult(
        status=outcome.status,
        answer=outcome.answer,
        clarifying_question=outcome.clarifying_question,
        message=outcome.message,
        grounding=run.grounding,
        tool_calls=run.traces,
        steps=run.steps,
        usage=UsageOut(input_tokens=run.usage.input_tokens, output_tokens=run.usage.output_tokens,
                       estimated_cost_usd=cost),
        duration_ms=round((clock() - started) * 1000, 2),
    )
    logger.info(
        "question_completed",
        extra={
            "event": "question_completed",
            "status": result.status,
            "steps": result.steps,
            "tool_call_count": len(result.tool_calls),
            "grounding_passed": run.grounding.passed if run.grounding else None,
            "duration_ms": result.duration_ms,
            "input_tokens": run.usage.input_tokens,
            "output_tokens": run.usage.output_tokens,
            "estimated_cost_usd": cost,
            "provider": provider.name,
        },
    )
    return result


def _provider_error(message: str = "The planner failed to produce a valid next step.") -> _Outcome:
    return _Outcome("provider_error", message=message)


def _loop(
    question: str,
    conn: sqlite3.Connection,
    provider: Provider,
    run: _Run,
    deadline: float,
    clock: Callable[[], float],
    *,
    max_tool_calls: int,
    max_steps: int,
) -> _Outcome:
    messages: list[Message] = [UserMessage(question)]
    tools = tool_definitions()
    deadline_hit = _Outcome("limit_exceeded", message="Stopped: the time limit for answering was reached.")

    while True:
        if run.steps >= max_steps:
            return _Outcome("limit_exceeded", message=f"Stopped: the question needed more than {max_steps} steps.")
        if clock() >= deadline:
            return deadline_hit

        try:
            response = provider.next_turn(tuple(messages), tools)
        except Exception as exc:
            logger.error(
                "provider_failed",
                exc_info=(type(exc), exc, exc.__traceback__),
                extra={"event": "provider_failed", "provider": provider.name},
            )
            return _provider_error()
        run.steps += 1
        if not isinstance(response, ProviderResponse):
            return _provider_error()
        run.usage += response.usage
        if clock() >= deadline:
            return deadline_hit  # a turn that arrives late is discarded, not acted on

        turn = response.turn
        if isinstance(turn, FinalAnswer) and turn.text.strip():
            return _grounded_outcome(turn, messages, run)
        if isinstance(turn, Clarify) and turn.question.strip():
            return _Outcome("clarification_needed", clarifying_question=turn.question)
        if not isinstance(turn, ToolCalls) or not turn.calls:
            return _provider_error()

        if len(run.traces) + len(turn.calls) > max_tool_calls:
            # Refused as a whole: running part of a batch would leave a misleading trace.
            return _Outcome(
                "tool_call_limit_exceeded",
                message=f"Stopped: the planner asked for more than {max_tool_calls} tool calls.",
            )
        messages.append(AssistantToolCalls(turn.calls))
        for call in turn.calls:
            remaining = deadline - clock()
            if remaining <= 0:
                return deadline_hit
            result = execute_tool(
                conn, call.name, call.arguments, time_budget_seconds=min(TOOL_TIME_BUDGET_SECONDS, remaining)
            )
            run.traces.append(
                ToolCallTrace(
                    call_id=call.id,
                    tool=result.tool,
                    arguments=result.arguments,
                    ok=result.ok,
                    error_code=result.error.code if result.error else None,
                    duration_ms=result.duration_ms,
                )
            )
            # Errors are sent back too: the provider decides what to do with a failed tool call.
            messages.append(ToolResultMessage(call.id, result.model_dump()))


def _grounded_outcome(turn: FinalAnswer, messages: list[Message], run: _Run) -> _Outcome:
    """Return the answer only if every number in it comes from this run's tool output."""
    tool_results = [m.result for m in messages if isinstance(m, ToolResultMessage)]
    arguments = [trace.arguments for trace in run.traces if trace.arguments]
    report = check_grounding(turn.text, tool_results, question_arguments=arguments)
    run.grounding = GroundingOut(**report.model_dump())
    if not report.passed:
        logger.warning(
            "grounding_failed",
            extra={"event": "grounding_failed", "unsupported": report.unsupported,
                   "numbers_checked": report.numbers_checked},
        )
        return _Outcome(
            "grounding_failed",
            message="The answer contained figures that could not be verified against the data, so it was withheld.",
        )
    return _Outcome("out_of_scope" if turn.out_of_scope else "answered", answer=turn.text)
