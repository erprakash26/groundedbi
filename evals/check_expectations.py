"""Check the expected numbers in the question files against the data, without the planner.

    python -m evals.check_expectations [QUESTION_FILE ...]   (default: dev and held-out files)

Seeds a fresh database (seed 42, default range) and, for every question, recomputes the
values its expected tool calls would return by calling app.services.analytics directly.
Every money string ("$314,032") and percentage ("71.6%") in must_contain must be one of
those values, formatted the way the app formats them. Exit code 1 on any mismatch.

It only ever reads the question files; it never writes to them.

Limits, on purpose: only numbers are verified (names and phrases like "didn't" are not), and
a question without expected tools may not contain numbers at all (there is nothing to
recompute them from). If expected_arguments leaves out a dimension, every dimension is
tried, since the totals do not depend on it.
"""

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from app.db.connection import get_connection
from app.services import analytics, metrics
from app.services.analytics import BREAKDOWN_DIMENSIONS, CHANGE_DIMENSIONS, MAX_TOP_LIMIT, Period
from app.tools.results import format_cents
from evals.run_evals import QUESTION_FILES, SEED, SEED_MONTHS, SEED_START
from scripts.seed_data import seed_database

_MONEY = re.compile(r"-?\$[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?")
_PERCENT = re.compile(r"-?[0-9]+\.[0-9]+(?=%|$)")


def expected_numbers(question: dict[str, Any]) -> list[str]:
    """Money and percentage tokens in must_contain ("71.6%" -> "71.6")."""
    found: list[str] = []
    for text in question["must_contain"]:
        found += _MONEY.findall(text)
        found += _PERCENT.findall(_MONEY.sub(" ", text))
    return found


def _money(*cents: int) -> set[str]:
    return {format_cents(c) for c in cents}


def _signed(cents: int) -> set[str]:
    return _money(cents, abs(cents))


def _pct(value: Any) -> set[str]:
    return set() if value is None else {str(value), str(abs(value))}


def _period(args: dict[str, Any], start: str, end: str) -> Period:
    if start not in args or end not in args:
        raise KeyError(f"expected_arguments needs {start} and {end} to recompute the numbers")
    return Period(args[start], args[end])


def recompute(conn, tool: str, args: dict[str, Any]) -> set[str]:
    """Every display string the tool call would produce, computed from the service layer."""
    if tool == "get_revenue_by_month":
        return {format_cents(r.revenue_cents) for r in analytics.revenue_by_month(conn, _period(args, "start_month", "end_month"))}
    if tool == "get_revenue_breakdown":
        period = _period(args, "start_month", "end_month")
        dims = [args["dimension"]] if "dimension" in args else list(BREAKDOWN_DIMENSIONS)
        return {format_cents(r.revenue_cents) for d in dims for r in analytics.revenue_breakdown(conn, period, d)}
    if tool == "get_top_products":
        period = _period(args, "start_month", "end_month")
        return {format_cents(r.revenue_cents) for r in analytics.top_products(conn, period, args.get("limit", MAX_TOP_LIMIT))}
    if tool == "compare_periods":
        a = _period(args, "period_a_start", "period_a_end")
        b = _period(args, "period_b_start", "period_b_end")
        result = analytics.compare_periods(conn, a, b)
        return _money(result.revenue_a_cents, result.revenue_b_cents) | _signed(result.change_cents) | _pct(result.pct_change)
    if tool == "get_change_drivers":
        a = _period(args, "period_a_start", "period_a_end")
        b = _period(args, "period_b_start", "period_b_end")
        values: set[str] = set()
        for dim in [args["dimension"]] if "dimension" in args else list(CHANGE_DIMENSIONS):
            drivers = analytics.change_drivers(conn, a, b, dim)
            total_a = sum(d.revenue_a_cents for d in drivers)
            total_b = sum(d.revenue_b_cents for d in drivers)
            values |= _money(total_a, total_b) | _signed(total_b - total_a) | _pct(metrics.pct_change(total_a, total_b))
            for d in drivers:
                values |= _money(d.revenue_a_cents, d.revenue_b_cents) | _signed(d.change_cents)
                values |= _pct(d.share_of_total_change)
        return values
    raise KeyError(f"no recomputation rule for tool {tool!r}")


def check_question(conn, question: dict[str, Any]) -> list[str]:
    """Problems with one question's expected numbers ([] if none)."""
    numbers = expected_numbers(question)
    if not numbers:
        return []
    tools = question["expected_tools"]
    if not tools:
        return [f"has numbers {numbers} but no expected tools to recompute them from"]
    expected_args = question["expected_arguments"]
    per_call = expected_args if isinstance(expected_args, list) else [expected_args] * len(tools)
    if len(per_call) != len(tools):
        return ["expected_arguments list length does not match expected_tools"]
    available: set[str] = set()
    try:
        for tool, args in zip(tools, per_call):
            available |= recompute(conn, tool, args)
    except (KeyError, ValueError) as exc:
        return [f"cannot recompute: {exc}"]
    return [f"{number} is not produced by {tools} with {expected_args}" for number in numbers if number not in available]


def check_files(paths: list[Path]) -> int:
    """Check every question in every file; print a report; return the exit code."""
    problems = 0
    checked_numbers = 0
    with tempfile.TemporaryDirectory(prefix="groundedbi-expect-") as folder:
        db_path = Path(folder) / "expect.db"
        seed_database(db_path, seed=SEED, start=SEED_START, months=SEED_MONTHS)
        conn = get_connection(db_path, read_only=True)
        try:
            for path in paths:
                questions = json.loads(path.read_text(encoding="utf-8"))["questions"]  # read only
                print(f"{path.name}: {len(questions)} questions")
                for question in questions:
                    numbers = expected_numbers(question)
                    issues = check_question(conn, question)
                    checked_numbers += len(numbers)
                    if issues:
                        problems += len(issues)
                        for issue in issues:
                            print(f"  MISMATCH {question['id']}: {issue}")
                    elif numbers:
                        print(f"  ok       {question['id']}: {', '.join(numbers)}")
                    else:
                        print(f"  ok       {question['id']}: no numbers to check")
        finally:
            conn.close()  # before the folder is removed: Windows cannot delete an open file
    print(f"\nChecked {checked_numbers} expected numbers against seed {SEED}: {problems} problem(s).")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", type=Path, help="question files (default: dev and held-out)")
    args = parser.parse_args(argv)
    return check_files(args.files or list(QUESTION_FILES.values()))


if __name__ == "__main__":
    sys.exit(main())
