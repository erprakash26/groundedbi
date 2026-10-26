"""Pure scoring functions for the eval runner: no database, no files, no clock.

A question's checks, and when each one applies:
  status            always: expected_status == actual status
  tools             always: exact ordered list of main tools (get_data_overview excluded); [] is an expectation too
  arguments         only if tools are correct and expected_arguments is not empty
  must_contain      only if the list is not empty: every string is in the returned text
  must_not_contain  only if the list is not empty: no string is in the returned text
  grounding         only if the actual status is "answered": the grounding check passed
A question passes when every applicable check passes. A check that does not apply is None.

Confident wrong answer: status "answered" and grounding passed, yet the question failed. It
is the worst kind of failure (a verified-looking answer that is wrong), so it is counted
separately.
"""

import math
from typing import Any

OVERVIEW_TOOL = "get_data_overview"
CHECKS = ("status", "tools", "arguments", "must_contain", "must_not_contain", "grounding")
REQUIRED_FIELDS = {
    "id", "split", "category", "question", "expected_status", "expected_tools",
    "expected_arguments", "must_contain", "must_not_contain", "notes",
}  # fmt: skip


def validate_questions(questions: list[dict[str, Any]]) -> None:
    """Raise ValueError if a question is missing a field or ids repeat."""
    ids = [q.get("id") for q in questions]
    if len(set(ids)) != len(ids):
        raise ValueError("question ids must be unique")
    for question in questions:
        missing = REQUIRED_FIELDS - set(question)
        if missing:
            raise ValueError(f"{question.get('id')}: missing fields {sorted(missing)}")


def returned_text(result: dict[str, Any]) -> str:
    """What the user would read: the answer, else the clarifying question, else the message."""
    return result.get("answer") or result.get("clarifying_question") or result.get("message") or ""


def main_tool_calls(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [call for call in result["tool_calls"] if call["tool"] != OVERVIEW_TOOL]


def check_arguments(expected: dict[str, Any] | list[dict[str, Any]], calls: list[dict[str, Any]]) -> bool:
    """Listed keys only. A dict applies to every call; a list has one dict per call, in order."""
    per_call = expected if isinstance(expected, list) else [expected] * len(calls)
    if len(per_call) != len(calls):
        return False
    for wanted, call in zip(per_call, calls):
        actual = call["arguments"] or {}  # None when validation failed: nothing matches
        if any(key not in actual or actual[key] != value for key, value in wanted.items()):
            return False
    return True


def score_question(question: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Score one AskResult (as a dict) against one labelled question."""
    text = returned_text(result)
    calls = main_tool_calls(result)
    actual_tools = [call["tool"] for call in calls]
    checks: dict[str, bool | None] = dict.fromkeys(CHECKS)
    checks["status"] = result["status"] == question["expected_status"]
    checks["tools"] = actual_tools == question["expected_tools"]
    if checks["tools"] and question["expected_arguments"]:
        checks["arguments"] = check_arguments(question["expected_arguments"], calls)
    if question["must_contain"]:
        checks["must_contain"] = all(s in text for s in question["must_contain"])
    if question["must_not_contain"]:
        checks["must_not_contain"] = not any(s in text for s in question["must_not_contain"])
    if result["status"] == "answered":
        checks["grounding"] = bool(result["grounding"] and result["grounding"]["passed"])

    passed = all(value is not False for value in checks.values())
    confident_wrong = result["status"] == "answered" and checks["grounding"] is True and not passed
    return {
        "id": question["id"],
        "split": question["split"],
        "category": question["category"],
        "question": question["question"],
        "passed": passed,
        "confident_wrong": confident_wrong,
        "checks": checks,
        "failed_checks": [name for name, value in checks.items() if value is False],
        "expected": {
            "status": question["expected_status"],
            "tools": question["expected_tools"],
            "arguments": question["expected_arguments"],
            "must_contain": question["must_contain"],
            "must_not_contain": question["must_not_contain"],
        },
        "actual": {
            "status": result["status"],
            "tools": actual_tools,
            "arguments": [call["arguments"] for call in calls],
            "text": text,
            "grounding": result["grounding"],
        },
        "missing_strings": [s for s in question["must_contain"] if s not in text],
        "forbidden_strings_found": [s for s in question["must_not_contain"] if s in text],
        "duration_ms": result["duration_ms"],
        "tool_call_count": len(result["tool_calls"]),
    }


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile: always one of the measured values (no interpolation). None if empty."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def _rate(passed: int, total: int) -> float | None:
    return round(passed / total, 4) if total else None


def _check_rate(scored: list[dict[str, Any]], check: str) -> dict[str, Any]:
    applicable = [s["checks"][check] for s in scored if s["checks"][check] is not None]
    passed = sum(applicable)
    return {"passed": passed, "total": len(applicable), "rate": _rate(passed, len(applicable))}


def aggregate(scored: list[dict[str, Any]]) -> dict[str, Any]:
    """Metrics for a group of scored questions. Each rate is over the questions where it applies."""
    durations = [s["duration_ms"] for s in scored]
    passed = sum(s["passed"] for s in scored)
    return {
        "questions": len(scored),
        "pass": {"passed": passed, "total": len(scored), "rate": _rate(passed, len(scored))},
        "status_accuracy": _check_rate(scored, "status"),
        "tool_accuracy": _check_rate(scored, "tools"),
        "argument_accuracy": _check_rate(scored, "arguments"),
        "grounding_pass_rate": _check_rate(scored, "grounding"),
        "confident_wrong": sum(s["confident_wrong"] for s in scored),
        "latency_ms": {
            "p50": percentile(durations, 50),
            "p95": percentile(durations, 95),
            "max": max(durations) if durations else None,
        },
        "avg_tool_calls": round(sum(s["tool_call_count"] for s in scored) / len(scored), 2) if scored else None,
    }


def aggregate_by(scored: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    """aggregate() per value of key ("split" or "category"), sorted by that value."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in scored:
        groups.setdefault(item[key], []).append(item)
    return {name: aggregate(groups[name]) for name in sorted(groups)}
