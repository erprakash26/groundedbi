"""check_grounding: is every number in an answer copied exactly from this run's tool output?

The orchestrator runs this on every final answer and withholds any answer that fails, so
GroundedBI never returns a figure it cannot trace back to a tool result.

What may appear in an answer, all matched as EXACT strings:
  - money ("$224,910", "-$224,910"): a *_display or *_abs_display value from the results
  - percentages ("71.6%", "-17.2%"): a pct_change, pct_change_abs or share_of_total_change value
  - numbers inside known labels: names from the results ("StudioBook 16"), month labels
    ("March 2026"), quarter labels ("Q2 2026") and years, but only for months that actually
    appear in the results or the tool arguments
  - bare integers the tools were actually called with (the 5 in "Top 5 products")
There is no approximate or sign-insensitive matching: "$224,910" where only "-$224,910" exists,
"$224,910.00", "$225K" or "17%" for "17.2" all fail.

Limitations (to be copied into the README):
  - Rounding trade-off: display strings are whole dollars rounded half-up, while the exact
    integer cents stay in the tool data. Rounded parts do not always add up to the rounded
    total (two drivers shown as $1 each can sit under a total shown as $3). Answers only quote
    displayed values, so they are consistent with the tool output, not re-added.
  - Numbers are checked, words are not. Once "$224,910" is allowed (as an *_abs_display), the
    check cannot tell "fell by $224,910" from "rose by $224,910". Direction words are protected
    separately: the rule-based templates always derive them from the sign of the change in
    code, never from the question, and tests cover that (including false premises).
"""

import re
from collections.abc import Callable, Iterator
from typing import Any

from pydantic import BaseModel

_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip
_PERCENT_KEYS = {"pct_change", "pct_change_abs", "share_of_total_change"}
_LABEL_KEYS = {"label", "products", "regions", "categories"}

# [0-9] in the allow-patterns: only ASCII digits can ever match an allowed string. The final
# _ANY_NUMBER also has \d+ so non-ASCII digits ("٢٠٢٦") are still found, and then fail.
_MONTH_LABEL = re.compile(r"\b(" + "|".join(_MONTH_NAMES) + r") ([0-9]{4})\b")
_QUARTER_LABEL = re.compile(r"\bQ([1-4]) ([0-9]{4})\b")
# Thousands groups are [0-9]{3} after a comma, so the comma ending a clause ("$79,968, then")
# is not swallowed into the number.
_NUMBER = r"[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?"
_MONEY = re.compile(rf"-?\${_NUMBER}[KkMm]?")
_PERCENT = re.compile(rf"-?{_NUMBER}%")
_YEAR = re.compile(r"\b(?:19|20)[0-9]{2}\b")
_ANY_NUMBER = re.compile(rf"-?{_NUMBER}|\d+")
_YYYY_MM = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])")


class GroundingReport(BaseModel):
    passed: bool
    numbers_checked: int
    unsupported: list[str]  # the tokens that could not be traced to tool output


def _leaves(obj: Any, key: str | None = None) -> Iterator[tuple[str | None, Any]]:
    """(key, value) for every leaf of a nested structure; list items keep their list's key."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _leaves(v, k)
    elif isinstance(obj, list):
        for item in obj:
            yield from _leaves(item, key)
    else:
        yield key, obj


def check_grounding(
    answer: str, tool_results: list[dict[str, Any]], *, question_arguments: list[dict[str, Any]]
) -> GroundingReport:
    leaves = list(_leaves(tool_results)) + list(_leaves(question_arguments))
    money = {v for k, v in leaves if isinstance(k, str) and k.endswith("_display") and isinstance(v, str)}
    percents = {v + "%" for k, v in leaves if k in _PERCENT_KEYS and isinstance(v, str)}
    labels = {v for k, v in leaves if k in _LABEL_KEYS and isinstance(v, str) and v}
    months = {v for _, v in leaves if isinstance(v, str) and _YYYY_MM.fullmatch(v)}
    years = {month[:4] for month in months}
    integers = {
        str(value)
        for args in question_arguments
        for value in args.values()
        if isinstance(value, int) and not isinstance(value, bool)
    }

    checked = 0
    unsupported: list[str] = []

    def check(pattern: re.Pattern[str], text: str, allowed: Callable[[re.Match[str]], bool]) -> str:
        """Check every match of pattern with allowed(match) and blank it out of the text."""

        def replace(match: re.Match[str]) -> str:
            nonlocal checked
            checked += 1
            if not allowed(match):
                unsupported.append(match[0])
            return " "

        return pattern.sub(replace, text)

    # Labels first and longest first, so "StudioBook 16 / West" is removed before "West".
    text = answer
    for label in sorted(labels, key=len, reverse=True):
        text = text.replace(label, " ")
    text = check(_MONTH_LABEL, text, lambda m: f"{m[2]}-{_MONTH_NAMES.index(m[1]) + 1:02d}" in months)
    text = check(_QUARTER_LABEL, text, lambda m: f"{m[2]}-{int(m[1]) * 3 - 2:02d}" in months)
    text = check(_MONEY, text, lambda m: m[0] in money)
    text = check(_PERCENT, text, lambda m: m[0] in percents)
    text = check(_YEAR, text, lambda m: m[0] in years)
    text = check(_ANY_NUMBER, text, lambda m: m[0] in integers)
    return GroundingReport(passed=not unsupported, numbers_checked=checked, unsupported=unsupported)
