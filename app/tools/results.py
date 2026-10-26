"""What every tool call returns: a ToolResult, success or failure, always JSON-serializable.

Units inside ToolResult.data:
  - Money keeps integer cents AND gets a display string next to it:
      "change_cents": -22491000, "change_display": "-$224,910.00"
    The LLM is told to quote the display strings, so an answer's numbers can later be
    matched exactly against tool output (the grounding check).
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
    """-22491000 -> "-$224,910.00". Integer arithmetic only: no float rounding can creep in."""
    sign = "-" if cents < 0 else ""
    dollars, remainder = divmod(abs(cents), 100)
    return f"{sign}${dollars:,}.{remainder:02d}"


def with_display(row: dict[str, Any]) -> dict[str, Any]:
    """Copy of row where every "<name>_cents" key is followed by a "<name>_display" string."""
    out: dict[str, Any] = {}
    for key, value in row.items():
        out[key] = value
        if key.endswith("_cents"):
            out[key.removesuffix("_cents") + "_display"] = format_cents(value)
    return out


def format_pct(value: Decimal | None) -> str | None:
    """Decimal("-17.2") -> "-17.2". A string keeps the exact value; a float could not."""
    return None if value is None else str(value)
