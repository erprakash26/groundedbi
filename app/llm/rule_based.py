"""RuleBasedProvider: the deterministic planner GroundedBI runs on (no LLM API key is used).

It implements the same Provider interface an LLM provider would, so it can be swapped out
without touching the orchestrator or the tools.

Stateless: every turn is worked out from the message history alone.
  1. No tool calls yet         -> refuse right away if the data can never answer the question
                                  (instructions, forecasts, only unsupported metrics); otherwise
                                  call get_data_overview, so months without a year resolve
                                  against the data, never against today's date.
  2. Only the overview so far  -> triage: out of scope (unrelated), Clarify (mixed metrics,
                                  unknown names, relative time, no clear intent), or plan and
                                  call the main tool(s).
  3. Main results received     -> write the answer from templates.
Because nothing is stored on the instance, one provider can serve concurrent requests and
the same messages always produce the same turn.

Grounding rule for the templates: every number in an answer is COPIED from a *_display,
*_abs_display or percentage string in a tool result (or is a tool argument such as a limit).
Templates never compute or reformat numbers. Direction words ("fell", "rose") always come from
the sign of the change in code, never from the question.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.llm.base import (
    AssistantToolCalls,
    Clarify,
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
    claimed_direction,
    detect_dimension,
    detect_intent,
    detect_out_of_scope,
    find_names,
    find_unknown_names,
    find_unsupported_metric,
    asks_bottom_ranking,
    asks_single_product,
    has_analytics_vocabulary,
    has_relative_time,
    mentions_supported_metric,
    parse_periods,
    parse_top_n,
    previous_span,
    ranked_dimension,
    unexplained_words,
)

OVERVIEW_CALL_ID = "call_1"
MAX_TOP_N = 20  # the get_top_products schema maximum

CAPABILITIES = (
    "I can explain what drove a revenue change, compare two periods, show monthly revenue,"
    " break revenue down by product, region or category, and list the top products."
)
NO_DATA_ANSWER = "There is no sales data in the database yet, so I can't answer questions about it."
_OUT_OF_SCOPE_REASONS = {
    "data_change": "I can't change, delete or add data; I can only read the sales data.",
    "instructions": "I can't follow instructions or run database commands; I only answer questions about the sales data.",
    "creative": "I can't write poems, stories or other creative pieces; I only answer questions about the sales data.",
    "forecast": "I can't make forecasts or predictions; I only report what is in the historical sales data.",
    "unrelated": "That question is outside what I can help with; I only answer questions about the sales data.",
}
_DIMENSION_PLURAL = {
    "product": "products",
    "region": "regions",
    "category": "categories",
    "product_region": "product/region pairs",
}
_DIMENSION_SINGULAR = {
    "product": "product",
    "region": "region",
    "category": "category",
    "product_region": "product/region pair",
}


@dataclass(frozen=True)
class PlannedCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Plan:
    intent: str  # template: change, named_change, compare, top, rank_dimension, monthly, breakdown
    calls: tuple[PlannedCall, ...]
    highlight: str | None = None  # a product/region/category named in the question
    highlight_kind: str | None = None  # "product", "region" or "category"
    note: str | None = None  # explains a default the planner chose (no period given, capped limit)
    claimed: str | None = None  # "down"/"up" if the question presumes a direction


# --- labels ----------------------------------------------------------------------


def month_label(month: str) -> str:
    """"2026-03" -> "March 2026"."""
    return f"{MONTH_NAMES[int(month[5:7]) - 1]} {month[:4]}"


def _shape(span: MonthSpan) -> str:
    """"month", "quarter" (a calendar quarter), "year" (January to December) or "range"."""
    start_month, end_month = int(span.start[5:7]), int(span.end[5:7])
    same_year = span.start[:4] == span.end[:4]
    if span.start == span.end:
        return "month"
    if same_year and start_month % 3 == 1 and end_month == start_month + 2:
        return "quarter"
    if same_year and (start_month, end_month) == (1, 12):
        return "year"
    return "range"


def short_label(span: MonthSpan) -> str:
    """"March 2026", "Q2 2026", "2026" or "January 2026 to April 2026"."""
    shape = _shape(span)
    if shape == "month":
        return month_label(span.start)
    if shape == "quarter":
        return f"Q{int(span.end[5:7]) // 3} {span.start[:4]}"
    if shape == "year":
        return span.start[:4]
    return f"{month_label(span.start)} to {month_label(span.end)}"


def period_label(span: MonthSpan) -> str:
    """Like short_label, but quarters and years also name their months: "Q2 2026 (April to June)"."""
    if _shape(span) in ("quarter", "year"):
        months = f"{MONTH_NAMES[int(span.start[5:7]) - 1]} to {MONTH_NAMES[int(span.end[5:7]) - 1]}"
        return f"{short_label(span)} ({months})"
    return short_label(span)


def _span(period: dict[str, str]) -> MonthSpan:
    return MonthSpan(period["start_month"], period["end_month"])


def _join(items: list[str], last: str = "and") -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" {last} " + items[-1]


# --- planning --------------------------------------------------------------------


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
    if coverage is None:
        return None
    last_month = coverage["last_month"]
    full_range = MonthSpan(coverage["first_month"], last_month)
    spans = parse_periods(question, last_month)
    highlight, kind = _highlight(question, overview)
    stated_dimension = detect_dimension(question)
    intent = detect_intent(question)
    if intent is None:
        # The total intent ("Revenue in April 2026", "revenue in the west in march") is used only
        # when every content word is understood. Anything else returns None and the caller asks:
        # an intent is never picked silently.
        known = overview["products"] + overview["regions"] + overview["categories"]
        if not (mentions_supported_metric(question) and (spans or highlight)) or unexplained_words(question, known):
            return None
        intent = "breakdown" if highlight else "monthly"
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
        if highlight:
            # A change or comparison about one named item is answered with that item's own row.
            # compare_periods (or the company headline of the drivers) answers a different
            # question. All rows of one dimension fit in 20, so the item's row is always there.
            intent = "named_change"
            args = {**_two_period_args(a, b), "dimension": kind, "limit": MAX_TOP_N}
            calls = (PlannedCall("get_change_drivers", args),)
        elif intent == "change":
            dimension = stated_dimension or kind or "product_region"
            calls = (PlannedCall("get_change_drivers", {**_two_period_args(a, b), "dimension": dimension}),)
        else:
            calls = (PlannedCall("compare_periods", _two_period_args(a, b)),)
    elif intent == "top" and ranked_dimension(question) and "product" not in question.lower():
        # "Which region had the highest revenue?": no top-N tool for regions or categories, but
        # the breakdown is already sorted by revenue, highest first.
        intent = "rank_dimension"
        span = MonthSpan(min(s.start for s in spans), max(s.end for s in spans)) if spans else full_range
        if not spans:
            note = "No period was given, so this covers all available data."
        calls = (PlannedCall("get_revenue_breakdown", {**_span_args(span), "dimension": ranked_dimension(question)}),)
    elif intent == "top":
        targets = sorted(spans[:2]) or [full_range]  # two periods -> top products for each
        if not spans:
            note = "No period was given, so this covers all available data."
        limit = 1 if asks_single_product(question) else parse_top_n(question)
        if limit > MAX_TOP_N:
            # The asked-for number is not repeated: it is not in any tool result, so grounding would reject it.
            limit = MAX_TOP_N
            note = f"That's more than the maximum, so this is showing up to the maximum of {MAX_TOP_N}."
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
    return Plan(intent, calls, highlight, kind, note, claimed_direction(question))


# --- clarifying questions and refusals -------------------------------------------------


def _latest_full_quarter(coverage: dict[str, str]) -> MonthSpan | None:
    year, month = int(coverage["last_month"][:4]), int(coverage["last_month"][5:7])
    quarter_end = month - month % 3
    if quarter_end == 0:
        year, quarter_end = year - 1, 12
    quarter = MonthSpan(f"{year:04d}-{quarter_end - 2:02d}", f"{year:04d}-{quarter_end:02d}")
    return quarter if quarter.start >= coverage["first_month"] else None


def _examples(coverage: dict[str, str]) -> list[str]:
    month = month_label(coverage["last_month"])
    quarter = _latest_full_quarter(coverage)
    return [
        f"Why did revenue change in {month}?",
        f"Top 5 products in {short_label(quarter) if quarter else month}",
        f"Revenue by region in {month}",
    ]


def _for_example(examples: list[str]) -> str:
    return " For example: " + _join([f'"{example}"' for example in examples], last="or") + "."


def out_of_scope_answer(reason: str, question: str) -> str:
    """A polite refusal with no numbers, saying what GroundedBI can answer instead."""
    if reason == "metric":
        lead = f"{find_unsupported_metric(question)} isn't in the data, so I can't answer that."
    else:
        lead = _OUT_OF_SCOPE_REASONS[reason]
    return f"{lead} {CAPABILITIES}"


def triage(question: str, overview: dict[str, Any]) -> Plan | Clarify | FinalAnswer:
    """After the overview: refuse, ask, or plan. Rules in order, first match wins."""
    coverage = overview["data_coverage"]
    known = overview["products"] + overview["regions"] + overview["categories"]
    examples = _examples(coverage)

    if not has_analytics_vocabulary(question, known):
        return FinalAnswer(out_of_scope_answer("unrelated", question), out_of_scope=True)

    unsupported = find_unsupported_metric(question)
    if unsupported:  # with a supported metric too (otherwise it was refused before the overview)
        spans = parse_periods(question, coverage["last_month"])
        period = short_label(spans[0]) if spans else month_label(coverage["last_month"])
        return Clarify(
            f"{unsupported} isn't in the data, but I can show revenue for {period}. Would you like that?"
            + _for_example([f"Revenue in {period}", f"Revenue by region in {period}"])
        )

    if asks_bottom_ranking(question):
        spans = parse_periods(question, coverage["last_month"])
        period = short_label(spans[0]) if spans else month_label(coverage["last_month"])
        return Clarify(
            "I can only rank from the top: no tool returns the lowest sellers."
            " Would you like the top products instead?" + _for_example([f"Top 5 products in {period}"])
        )

    unknown = find_unknown_names(question, known)
    if unknown:
        return Clarify(
            f'I couldn\'t find "{unknown[0]}" in the data. The regions are {_join(overview["regions"])};'
            f" the products are {_join(overview['products'])};"
            f" the categories are {_join(overview['categories'])}. Which one did you mean?"
            + _for_example(examples[2:] + examples[:1])
        )

    if has_relative_time(question):
        quarter = _latest_full_quarter(coverage)
        latest = f"the latest month in the data is {month_label(coverage['last_month'])}"
        if quarter:
            latest += f" and the latest full quarter is {period_label(quarter)}"
        return Clarify(f"Which period do you mean? I don't use today's date: {latest}." + _for_example(examples[:2]))

    plan = plan_question(question, overview)
    if plan is None:
        return _understood_clarify(question, overview, examples)
    return plan


def _understood_clarify(question: str, overview: dict[str, Any], examples: list[str]) -> Clarify:
    """No intent was recognized: say what WAS understood and offer the two likely readings."""
    coverage = overview["data_coverage"]
    spans = parse_periods(question, coverage["last_month"])
    highlight, _ = _highlight(question, overview)
    if not spans and not highlight:
        return Clarify("I'm not sure what you'd like to see. What would you like to know?" + _for_example(examples))
    span = spans[0] if spans else MonthSpan(coverage["last_month"], coverage["last_month"])
    period, previous = short_label(span), short_label(previous_span(span))
    subject = f"{highlight}'s revenue in {period}" if highlight else f"{period}'s total revenue"
    return Clarify(
        f"Do you want {subject}, or what drove its change from {previous}?"
        + _for_example([f"Revenue in {period}", f"Why did revenue change in {period}?"])
    )


# --- answer templates ------------------------------------------------------------


def _headline(
    change_cents: int,
    change_abs: str,
    pct_abs: str | None,
    total_a: str,
    total_b: str,
    a: str,
    b: str,
    claimed: str | None,
    subject: str = "Revenue",
) -> str:
    """Direction from the SIGN of change_cents; a question's wrong premise is corrected first."""
    pct = f" ({pct_abs}%)" if pct_abs is not None else ""
    actual = "rose" if change_cents > 0 else "fell" if change_cents < 0 else None
    contradicted = (claimed == "down" and change_cents >= 0) or (claimed == "up" and change_cents <= 0)
    if contradicted:
        denied = "drop" if claimed == "down" else "rise"
        if actual is None:
            return f"{subject} didn't {denied}: it was unchanged at {total_b} in {a} and {b}."
        return f"{subject} didn't {denied}: it {actual} by {change_abs}{pct} from {a} to {b} ({total_a} to {total_b})."
    if actual is None:
        return f"{subject} was unchanged at {total_b} in {a} and {b}."
    return f"{subject} {actual} by {change_abs}{pct}, from {total_a} in {a} to {total_b} in {b}."


def _change_answer(plan: Plan, data: dict[str, Any]) -> str:
    a, b = period_label(_span(data["period_a"])), period_label(_span(data["period_b"]))
    rows = data["rows"]
    if not rows:
        return f"There were no sales in {a} or {b}, so there is no change to explain."
    text = _headline(
        data["total_change_cents"], data["total_change_abs_display"], data["pct_change_abs"],
        data["total_revenue_a_display"], data["total_revenue_b_display"], a, b, plan.claimed,
    )  # fmt: skip
    first = rows[0]
    if first["change_cents"] != 0:
        verb = "fell" if first["change_cents"] < 0 else "rose"
        share = first["share_of_total_change"]
        share_text = f", {share}% of the total change" if share is not None else ""
        text += (
            f" The largest driver was {first['label']}, which {verb} by {first['change_abs_display']}"
            f" from {first['revenue_a_display']} to {first['revenue_b_display']}{share_text}."
        )
    if len(rows) > 1 and rows[1]["share_of_total_change"] is not None:
        nxt = rows[1]
        share = nxt["share_of_total_change"]
        singular = _DIMENSION_SINGULAR[data["dimension"]]
        if share.startswith("-"):
            text += f" The next largest, {nxt['label']} ({nxt['change_display']}), moved against the total ({share}% of the change)."
        else:
            # Rows are sorted by absolute change, so no later row can have a larger share.
            text += f" No other {singular} accounts for more than {share}% (next largest: {nxt['label']}, {nxt['change_display']})."
    return text


def _compare_answer(plan: Plan, data: dict[str, Any]) -> str:
    a, b = period_label(_span(data["period_a"])), period_label(_span(data["period_b"]))
    return _headline(
        data["change_cents"], data["change_abs_display"], data["pct_change_abs"],
        data["revenue_a_display"], data["revenue_b_display"], a, b, plan.claimed,
    )  # fmt: skip


def _top_list(data: dict[str, Any]) -> str:
    return "; ".join(f"{row['label']} ({row['revenue_display']})" for row in data["rows"])


def _named_change_answer(plan: Plan, data: dict[str, Any]) -> str:
    """One named item's own change: its revenue in each period, its change and its share."""
    a, b = period_label(_span(data["period_a"])), period_label(_span(data["period_b"]))
    name = plan.highlight
    row = next((r for r in data["rows"] if r["label"].lower() == name.lower()), None)
    if row is None:
        return f"{name} had no sales in {a} or {b}."
    text = _headline(
        row["change_cents"], row["change_abs_display"], None,
        row["revenue_a_display"], row["revenue_b_display"], a, b, plan.claimed, subject=f"{name} revenue",
    )  # fmt: skip
    if row["share_of_total_change"] is not None:
        text += f" That is {row['share_of_total_change']}% of the total change in revenue."
    # The company total follows in one sentence, after the item's own figures.
    total = data["total_change_cents"]
    plural = _DIMENSION_PLURAL[data["dimension"]]
    if total == 0:
        text += f" Across all {plural}, revenue was unchanged."
    else:
        verb = "rose" if total > 0 else "fell"
        pct = f" ({data['pct_change_abs']}%)" if data["pct_change_abs"] is not None else ""
        text += f" Across all {plural}, revenue {verb} by {data['total_change_abs_display']}{pct}."
    return text


def _rank_dimension_answer(plan: Plan, data: dict[str, Any]) -> str:
    """Highest revenue by region or category. A winner is named only if it is strictly ahead."""
    period = period_label(_span(data["period"]))
    rows = data["rows"]
    if not rows:
        return f"There were no sales in {period}."
    first = rows[0]
    # Rows are sorted by revenue, highest first, so ties sit together at the top.
    tied = [r for r in rows if r["revenue_cents"] == first["revenue_cents"]]
    if len(tied) == 1:
        lead = f"{first['label']} had the highest revenue in {period}: {first['revenue_display']}."
    else:
        lead = f"{_join([r['label'] for r in tied])} tied for the highest revenue in {period}: {first['revenue_display']} each."
    ranking = "; ".join(f"{row['label']} {row['revenue_display']}" for row in rows)
    return f"{lead} Full ranking by {data['dimension']}: {ranking}."


def _top_answer(plan: Plan, results: list[dict[str, Any]]) -> str:
    limit = results[0]["arguments"]["limit"]
    if limit == 1 and len(results) == 1:
        data = results[0]["data"]
        period = period_label(_span(data["period"]))
        if not data["rows"]:
            return f"There were no product sales in {period}."
        row = data["rows"][0]
        return f"The top product by revenue in {period} was {row['label']} ({row['revenue_display']})."
    # "Top 5" only when 5 rows came back; with fewer products than the limit, just "Top".
    full = all(len(r["data"]["rows"]) == limit for r in results)
    heading = f"Top {limit} products by revenue" if full else "Top products by revenue"
    if len(results) == 1:
        data = results[0]["data"]
        period = period_label(_span(data["period"]))
        if not data["rows"]:
            return f"There were no product sales in {period}."
        return f"{heading} in {period}: {_top_list(data)}."
    lines = [f"{heading}:"]
    for result in results:
        data = result["data"]
        listed = _top_list(data) if data["rows"] else "no product sales"
        lines.append(f"{short_label(_span(data['period']))}: {listed}.")
    return "\n".join(lines)


def _monthly_answer(plan: Plan, data: dict[str, Any]) -> str:
    span = _span(data["period"])
    period = period_label(span)
    rows = data["rows"]
    if not rows:
        return f"There were no sales in {period}."
    if span.start == span.end:
        return f"Revenue in {period} was {rows[0]['revenue_display']}."
    listed = "; ".join(f"{month_label(row['month'])} {row['revenue_display']}" for row in rows)
    return f"Monthly revenue for {period}: {listed}."


def _breakdown_answer(plan: Plan, data: dict[str, Any]) -> str:
    period = period_label(_span(data["period"]))
    if not data["rows"]:
        return f"There were no sales in {period}."
    listed = "; ".join(f"{row['label']} {row['revenue_display']}" for row in data["rows"])
    return f"Revenue by {data['dimension']} for {period}: {listed}."


_SINGLE_RESULT_WRITERS = {
    "change": _change_answer,
    "named_change": _named_change_answer,
    "rank_dimension": _rank_dimension_answer,
    "compare": _compare_answer,
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
    months = _join([month_label(month) for month in sorted(set(uncovered))])
    more = " and further months" if truncated else ""
    coverage = results[0]["data"]["data_coverage"]
    return (
        f"There is no data for {months}{more};"
        f" the data covers {month_label(coverage['first_month'])} to {month_label(coverage['last_month'])}."
    )


def _highlight_sentence(plan: Plan, results: list[dict[str, Any]]) -> str | None:
    if plan.highlight is None or plan.intent == "named_change":
        return None  # a named_change answer is already about the named item
    name = plan.highlight
    # What the rows are labelled by. compare and monthly return totals only: nothing to highlight.
    row_dimension = results[0]["data"].get("dimension") or ("product" if plan.intent == "top" else None)
    if row_dimension != plan.highlight_kind:
        return f"These figures cover all products and regions; I can't filter them to {name}."
    parts = [f"This shows all {_DIMENSION_PLURAL[row_dimension]} with {name} highlighted; it is not filtered to {name}."]
    rows = results[0]["data"]["rows"]
    if plan.intent == "change" and rows and rows[0]["label"].lower() == name.lower():
        return parts[0]  # already the headline driver: don't repeat its figures
    for result in results:
        data = result["data"]
        row = next((r for r in data["rows"] if r["label"].lower() == name.lower()), None)
        where = f" in {short_label(_span(data['period']))}" if len(results) > 1 else ""
        if row is None:
            parts.append(f"{name} is not among the rows returned{where}.")
        elif plan.intent == "change":
            share = row["share_of_total_change"]
            share_text = f" ({share}% of the total change)" if share is not None else ""
            parts.append(f"{name}: {row['change_display']}{share_text}.")
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
    if plan.intent == "top":
        main = _top_answer(plan, results)
    else:
        main = _SINGLE_RESULT_WRITERS[plan.intent](plan, results[0]["data"])
    extras = [s for s in (_highlight_sentence(plan, results), _coverage_sentence(results), plan.note) if s]
    return " ".join([main, *extras]) if "\n" not in main else "\n".join([main, *extras])


# --- the provider ----------------------------------------------------------------


class RuleBasedProvider:
    name = "rule_based"
    pricing = Pricing()  # no tokens, no cost

    def next_turn(self, messages: Sequence[Message], tools: Sequence[dict[str, Any]]) -> ProviderResponse:
        question = next((m.text for m in messages if isinstance(m, UserMessage)), "")
        calls = [call for m in messages if isinstance(m, AssistantToolCalls) for call in m.calls]
        results = {m.call_id: m.result for m in messages if isinstance(m, ToolResultMessage)}

        if not calls:
            reason = detect_out_of_scope(question)
            if reason:  # no data could ever answer this: refuse without calling any tool
                return _turn(FinalAnswer(out_of_scope_answer(reason, question), out_of_scope=True))
            return _turn(ToolCalls((ToolCall(OVERVIEW_CALL_ID, "get_data_overview", {}),)))
        overview = results.get(calls[0].id)
        if overview is None or not overview["ok"]:
            return _turn(FinalAnswer(error_answer(overview)))
        if overview["data"]["data_coverage"] is None:
            return _turn(FinalAnswer(NO_DATA_ANSWER))
        # Re-decided every turn from the question and overview: same input, same decision, no state kept.
        decision = triage(question, overview["data"])
        if not isinstance(decision, Plan):
            return _turn(decision)
        if len(calls) == 1:
            return _turn(
                ToolCalls(
                    tuple(
                        ToolCall(f"call_{number}", planned.name, dict(planned.arguments))
                        for number, planned in enumerate(decision.calls, start=2)
                    )
                )
            )
        main_results = [results.get(call.id) for call in calls[1:]]
        if any(result is None for result in main_results):
            return _turn(FinalAnswer(error_answer(None)))
        return _turn(FinalAnswer(write_answer(decision, main_results)))


def _turn(turn: ToolCalls | FinalAnswer | Clarify) -> ProviderResponse:
    return ProviderResponse(turn)  # Usage() defaults to zero tokens
