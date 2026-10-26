"""RuleBasedProvider: the deterministic planner GroundedBI runs on (no LLM API key is used).

It implements the same Provider interface an LLM provider would, so it can be swapped out
without touching the orchestrator or the tools.

Stateless: every turn is worked out from the message history alone.
  1. No tool calls yet         -> call get_data_overview (months without a year are then
                                  resolved against the data, never against today's date).
  2. Only the overview so far  -> parse the question and call the main tool(s), or answer
                                  that the question is not supported yet.
  3. Main results received     -> write the answer from templates.
Because nothing is stored on the instance, one provider can serve concurrent requests and
the same messages always produce the same turn.

Grounding rule for the templates: every number in an answer is COPIED from a *_display or
percentage string in a tool result. Templates never compute or reformat numbers. Month
names ("March 2026") are spelled out from the YYYY-MM strings in the results.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.llm.base import (
    AssistantToolCalls,
    FinalAnswer,
    Message,
    Pricing,
    ProviderResponse,
    ToolCall,
    ToolCalls,
    ToolResultMessage,
    UserMessage,
)
from app.llm.question_parser import (
    MONTH_NAMES,
    MonthSpan,
    detect_dimension,
    detect_intent,
    find_names,
    parse_periods,
    parse_top_n,
    previous_span,
)

OVERVIEW_CALL_ID = "call_1"

UNSUPPORTED_ANSWER = (
    "Sorry, I can't answer that question yet. I can explain what drove a revenue change, compare"
    " two periods, show revenue by month, break revenue down by product, region or category, and"
    " list the top products."
)
NO_DATA_ANSWER = "There is no sales data in the database yet, so I can't answer questions about it."
_DIMENSION_PLURAL = {
    "product": "products",
    "region": "regions",
    "category": "categories",
    "product_region": "product/region pairs",
}


@dataclass(frozen=True)
class PlannedCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Plan:
    intent: str
    calls: tuple[PlannedCall, ...]
    highlight: str | None = None  # a product/region/category named in the question
    highlight_kind: str | None = None  # "product", "region" or "category"
    note: str | None = None  # explains a default the planner chose (e.g. no period given)


# --- planning --------------------------------------------------------------


def _highlight(question: str, overview: dict[str, Any]) -> tuple[str | None, str | None]:
    """The first named product, else region, else category. Tools cannot filter by name, so a
    name only steers the dimension and is highlighted in the answer."""
    for kind, key in (("product", "products"), ("region", "regions"), ("category", "categories")):
        names = find_names(question, overview[key])
        if names:
            return names[0], kind
    return None, None


def _span_args(span: MonthSpan) -> dict[str, str]:
    return {"start_month": span.start, "end_month": span.end}


def _two_period_args(a: MonthSpan, b: MonthSpan) -> dict[str, str]:
    return {"period_a_start": a.start, "period_a_end": a.end, "period_b_start": b.start, "period_b_end": b.end}


def plan_question(question: str, overview: dict[str, Any]) -> Plan | None:
    """Which tool(s) answer this question, with which arguments. None if no rule matches."""
    coverage = overview["data_coverage"]
    intent = detect_intent(question)
    if intent is None or coverage is None:
        return None
    last_month = coverage["last_month"]
    full_range = MonthSpan(coverage["first_month"], last_month)
    spans = parse_periods(question, last_month)
    highlight, kind = _highlight(question, overview)
    stated_dimension = detect_dimension(question)
    note = None

    if intent in ("change", "compare"):
        if len(spans) >= 2:
            a, b = sorted(spans[:2])  # chronological: A is always the earlier period
        elif len(spans) == 1:
            b = spans[0]
            a = previous_span(b)
        else:
            b = MonthSpan(last_month, last_month)
            a = previous_span(b)
            note = "No period was given, so this compares the latest month in the data with the month before."
        if intent == "change":
            dimension = stated_dimension or kind or "product_region"
            calls = (PlannedCall("get_change_drivers", {**_two_period_args(a, b), "dimension": dimension}),)
        else:
            calls = (PlannedCall("compare_periods", _two_period_args(a, b)),)
    elif intent == "top":
        targets = sorted(spans[:2]) or [full_range]  # two periods -> top products for each
        if not spans:
            note = "No period was given, so this covers all available data."
        limit = parse_top_n(question)
        calls = tuple(PlannedCall("get_top_products", {**_span_args(span), "limit": limit}) for span in targets)
    else:
        if not spans:
            span = full_range
            note = "No period was given, so this covers all available data."
        else:
            # Several periods for a single-period tool: cover them all, first to last.
            span = MonthSpan(min(s.start for s in spans), max(s.end for s in spans))
        if intent == "monthly":
            calls = (PlannedCall("get_revenue_by_month", _span_args(span)),)
        else:
            dimension = stated_dimension or kind or "product"
            calls = (PlannedCall("get_revenue_breakdown", {**_span_args(span), "dimension": dimension}),)
    return Plan(intent, calls, highlight, kind, note)


# --- answer templates ------------------------------------------------------


def month_label(month: str) -> str:
    """"2026-03" -> "March 2026"."""
    return f"{MONTH_NAMES[int(month[5:7]) - 1]} {month[:4]}"


def period_label(period: dict[str, str]) -> str:
    start, end = period["start_month"], period["end_month"]
    return month_label(start) if start == end else f"{month_label(start)} to {month_label(end)}"


def _share(row: dict[str, Any]) -> str:
    share = row["share_of_total_change"]
    return f"{share}% of the total change" if share is not None else "share undefined because the total change is zero"


def _change_answer(data: dict[str, Any]) -> str:
    a, b = period_label(data["period_a"]), period_label(data["period_b"])
    rows = data["rows"]
    if not rows:
        return f"There were no sales in {a} or {b}, so there is no change to explain."
    first = rows[0]
    text = (
        f"Revenue changed by {data['total_change_display']} from {a} to {b}."
        f" The largest driver was {first['label']}, which went from {first['revenue_a_display']}"
        f" to {first['revenue_b_display']} ({first['change_display']}, {_share(first)})."
    )
    others = "; ".join(f"{row['label']} {row['change_display']} ({_share(row)})" for row in rows[1:])
    if others:
        text += f" Next largest {_DIMENSION_PLURAL[data['dimension']]}: {others}."
    return text


def _compare_answer(data: dict[str, Any]) -> str:
    a, b = period_label(data["period_a"]), period_label(data["period_b"])
    text = (
        f"Revenue was {data['revenue_a_display']} in {a} and {data['revenue_b_display']} in {b},"
        f" a change of {data['change_display']}"
    )
    if data["pct_change"] is None:
        return text + f". There is no percentage change because {a} had no revenue."
    return text + f" ({data['pct_change']}%)."


def _top_answer(data: dict[str, Any]) -> str:
    period = period_label(data["period"])
    if not data["rows"]:
        return f"There were no product sales in {period}."
    listed = "; ".join(f"{row['label']} ({row['revenue_display']})" for row in data["rows"])
    return f"Top products by revenue in {period}: {listed}."


def _monthly_answer(data: dict[str, Any]) -> str:
    period = period_label(data["period"])
    if not data["rows"]:
        return f"There were no sales in {period}."
    listed = "; ".join(f"{month_label(row['month'])} {row['revenue_display']}" for row in data["rows"])
    return f"Monthly revenue for {period}: {listed}."


def _breakdown_answer(data: dict[str, Any]) -> str:
    period = period_label(data["period"])
    if not data["rows"]:
        return f"There were no sales in {period}."
    listed = "; ".join(f"{row['label']} {row['revenue_display']}" for row in data["rows"])
    return f"Revenue by {data['dimension']} for {period}: {listed}."


_WRITERS = {
    "change": _change_answer,
    "compare": _compare_answer,
    "top": _top_answer,
    "monthly": _monthly_answer,
    "breakdown": _breakdown_answer,
}


def _coverage_sentence(results: list[dict[str, Any]]) -> str | None:
    uncovered: list[str] = []
    truncated = False
    for result in results:
        data = result["data"]
        for key in ("uncovered_months", "period_a_uncovered_months", "period_b_uncovered_months"):
            uncovered += data.get(key, [])
            truncated |= key.removesuffix("months") + "month_count" in data
    if not uncovered:
        return None
    months = ", ".join(month_label(month) for month in sorted(set(uncovered)))
    more = " and further months" if truncated else ""
    coverage = results[0]["data"]["data_coverage"]
    return (
        f"There is no data for {months}{more};"
        f" the data covers {month_label(coverage['first_month'])} to {month_label(coverage['last_month'])}."
    )


def _highlight_sentence(plan: Plan, results: list[dict[str, Any]]) -> str | None:
    if plan.highlight is None:
        return None
    name = plan.highlight
    # What the rows are labelled by. compare and monthly return totals only: nothing to highlight.
    row_dimension = results[0]["data"].get("dimension") or ("product" if plan.intent == "top" else None)
    if row_dimension != plan.highlight_kind:
        return f"These figures cover all products and regions; I can't filter them to {name}."
    plural = _DIMENSION_PLURAL[row_dimension]
    parts = [f"This shows all {plural} with {name} highlighted; it is not filtered to {name}."]
    for result in results:
        row = next((r for r in result["data"]["rows"] if r["label"].lower() == name.lower()), None)
        where = f" in {period_label(result['data']['period'])}" if "period" in result["data"] and len(results) > 1 else ""
        if row is None:
            parts.append(f"{name} is not among the rows returned{where}.")
        elif plan.intent == "change":
            parts.append(f"{name}: {row['change_display']} ({_share(row)}).")
        else:
            parts.append(f"{name}{where}: {row['revenue_display']}.")
    return " ".join(parts)


def error_answer(result: dict[str, Any] | None) -> str:
    """No numbers: nothing from a failed call can be verified."""
    if result is None or result.get("error") is None:
        return "I couldn't complete this request because a tool result was missing."
    tool = result.get("tool") or "requested"
    return f"I couldn't complete this request: the {tool} tool returned the error \"{result['error']['code']}\"."


def write_answer(plan: Plan, results: list[dict[str, Any]]) -> str:
    failed = next((result for result in results if not result["ok"]), None)
    if failed is not None:
        return error_answer(failed)
    sentences = [_WRITERS[plan.intent](result["data"]) for result in results]
    for extra in (_highlight_sentence(plan, results), _coverage_sentence(results), plan.note):
        if extra:
            sentences.append(extra)
    return " ".join(sentences)


# --- the provider ----------------------------------------------------------


class RuleBasedProvider:
    name = "rule_based"
    pricing = Pricing()  # no tokens, no cost

    def next_turn(self, messages: Sequence[Message], tools: Sequence[dict[str, Any]]) -> ProviderResponse:
        question = next((m.text for m in messages if isinstance(m, UserMessage)), "")
        calls = [call for m in messages if isinstance(m, AssistantToolCalls) for call in m.calls]
        results = {m.call_id: m.result for m in messages if isinstance(m, ToolResultMessage)}

        if not calls:
            return _turn(ToolCalls((ToolCall(OVERVIEW_CALL_ID, "get_data_overview", {}),)))
        overview = results.get(calls[0].id)
        if overview is None or not overview["ok"]:
            return _turn(FinalAnswer(error_answer(overview)))
        if overview["data"]["data_coverage"] is None:
            return _turn(FinalAnswer(NO_DATA_ANSWER))
        # Re-planned every turn from the question and overview: same input, same plan, no state kept.
        plan = plan_question(question, overview["data"])
        if plan is None:
            return _turn(FinalAnswer(UNSUPPORTED_ANSWER))
        if len(calls) == 1:
            return _turn(
                ToolCalls(
                    tuple(
                        ToolCall(f"call_{number}", planned.name, dict(planned.arguments))
                        for number, planned in enumerate(plan.calls, start=2)
                    )
                )
            )
        main_results = [results.get(call.id) for call in calls[1:]]
        if any(result is None for result in main_results):
            return _turn(FinalAnswer(error_answer(None)))
        return _turn(FinalAnswer(write_answer(plan, main_results)))


def _turn(turn: ToolCalls | FinalAnswer) -> ProviderResponse:
    return ProviderResponse(turn)  # Usage() defaults to zero tokens
