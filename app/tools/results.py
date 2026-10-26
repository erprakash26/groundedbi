"""What every tool call returns: a ToolResult, success or failure, always JSON-serializable.

Units inside ToolResult.data:
  - Money keeps exact integer cents AND gets a display string in whole dollars, rounded half-up:
      "change_cents": -22491000, "change_display": "-$224,910", "change_abs_display": "$224,910"
    Every signed amount (any "*change*_cents" key) also gets an *_abs_display, so an answer can
    say "fell by $224,910" while still only copying strings. Answers quote the display strings,
    so the grounding check can match every number in an answer exactly against tool output.
  - Percentages are strings like "-17.2" (meaning -17.2%), or None when undefined.
"""

from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class ToolError(BaseModel):
    code: str  # stable, machine-readable: unknown_tool, invalid_arguments, tool_timeout, tool_failed
    message: str
    details: list[dict[str, Any]] | None = None  # field-level problems, only for invalid_arguments


class ToolResult(BaseModel):
    ok: bool
    tool: str | None  # None when the requested name is not a known tool (never echo unknown input)
    arguments: dict[str, Any] | None  # normalized, after validation; None if validation failed
    data: dict[str, Any] | None  # plain JSON types only (str, int, bool, None, list, dict)
    error: ToolError | None
    duration_ms: float


def format_cents(cents: int) -> str:
    """Whole dollars, rounded half-up: -22491000 -> "-$224,910", 149 -> "$1", 150 -> "$2".

    Integer arithmetic only, so no float rounding can creep in. Rounding is applied to the
    absolute value, so -150 -> "-$2" (symmetric), and anything that rounds to zero is "$0",
    never "-$0".
    """
    dollars, remainder = divmod(abs(cents), 100)
    if remainder >= 50:
        dollars += 1
    sign = "-" if cents < 0 and dollars else ""
    return f"{sign}${dollars:,}"


def with_display(row: dict[str, Any]) -> dict[str, Any]:
    """Copy of row where every "<name>_cents" key is followed by a "<name>_display" string,
    and every signed "*change*_cents" key also by a "<name>_abs_display" string."""
    out: dict[str, Any] = {}
    for key, value in row.items():
        out[key] = value
        if key.endswith("_cents"):
            name = key.removesuffix("_cents")
            out[name + "_display"] = format_cents(value)
            if "change" in name:
                out[name + "_abs_display"] = format_cents(abs(value))
    return out


def format_pct(value: Decimal | None) -> str | None:
    """Decimal("-17.2") -> "-17.2". A string keeps the exact value; a float could not."""
    return None if value is None else str(value)


def format_pct_abs(value: Decimal | None) -> str | None:
    """Decimal("-17.2") -> "17.2", for wording like "fell by $314,032 (17.2%)"."""
    return None if value is None else str(abs(value))
