"""execute_tool: run one named tool with JSON arguments. It never raises to its caller.

Every outcome, good or bad, is a ToolResult the LLM can read and react to:
  unknown_tool       the name is not one of the registered tools
  invalid_arguments  validation failed; details say which field and why, never the raw value
  tool_timeout       the SQL ran longer than the time budget and SQLite aborted it
  tool_failed        anything else; generic message to the caller, full traceback to the log
"""

import logging
import re
import sqlite3
import time
from typing import Any

from pydantic import ValidationError

from app.tools.definitions import TOOLS
from app.tools.results import ToolError, ToolResult

logger = logging.getLogger("app.tools")

DEFAULT_TIME_BUDGET_SECONDS = 2.0
# SQLite calls the progress handler every N virtual-machine instructions. Small enough to
# react within milliseconds, large enough that the Python callback costs almost nothing.
_PROGRESS_EVERY_N_INSTRUCTIONS = 1000
# An extra field's NAME is raw input too. Report it only if it looks like an ordinary identifier.
_SAFE_FIELD_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,63}")


def _validation_details(exc: ValidationError) -> list[dict[str, Any]]:
    """Where and why each field failed. Pydantic's input and ctx are dropped: they echo raw values."""
    details = []
    for error in exc.errors(include_url=False, include_input=False, include_context=False):
        field = ".".join(str(part) for part in error["loc"]) or None  # None: about the arguments as a whole
        if field is not None and not _SAFE_FIELD_PATTERN.fullmatch(field):
            field = "<unrecognized field>"
        details.append({"field": field, "message": error["msg"], "type": error["type"]})
    return details


def execute_tool(
    conn: sqlite3.Connection,
    name: Any,
    arguments: Any,
    *,
    time_budget_seconds: float = DEFAULT_TIME_BUDGET_SECONDS,
) -> ToolResult:
    """Validate the arguments, run the tool's handler under a time budget, and return a ToolResult."""
    started = time.perf_counter()
    tool = TOOLS.get(name) if isinstance(name, str) else None
    normalized: dict[str, Any] | None = None
    data: dict[str, Any] | None = None
    error: ToolError | None = None

    if tool is None:
        # The unknown name is not echoed: it is raw model output and could be anything.
        error = ToolError(code="unknown_tool", message=f"Unknown tool. Available tools: {', '.join(TOOLS)}.")
    else:
        try:
            args = tool.arguments_model.model_validate(arguments)
        except ValidationError as exc:
            error = ToolError(
                code="invalid_arguments",
                message=f"Arguments for {tool.name} are invalid.",
                details=_validation_details(exc),
            )
        else:
            normalized = args.model_dump(mode="json")
            data, error = _run_with_budget(conn, tool.name, tool.handler, args, time_budget_seconds)

    result = ToolResult(
        ok=error is None,
        tool=tool.name if tool else None,
        arguments=normalized,
        data=data,
        error=error,
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
    )
    logger.info(
        "tool_executed",
        extra={
            "event": "tool_executed",
            "tool": result.tool,
            "arguments": normalized,  # None when invalid: raw input is not logged either
            "ok": result.ok,
            "error_code": error.code if error else None,
            "duration_ms": result.duration_ms,
        },
    )
    return result


def _run_with_budget(
    conn: sqlite3.Connection, tool_name: str, handler: Any, args: Any, time_budget_seconds: float
) -> tuple[dict[str, Any] | None, ToolError | None]:
    # perf_counter, not monotonic: on Windows time.monotonic() only ticks every ~15 ms.
    deadline = time.perf_counter() + time_budget_seconds
    timed_out = False

    def abort_if_over_budget() -> int:
        # A non-zero return makes SQLite abort the running statement with OperationalError("interrupted").
        nonlocal timed_out
        if time.perf_counter() > deadline:
            timed_out = True
            return 1
        return 0

    conn.set_progress_handler(abort_if_over_budget, _PROGRESS_EVERY_N_INSTRUCTIONS)
    try:
        return handler(conn, args), None
    except sqlite3.OperationalError as exc:
        # Only OUR abort is a timeout; any other OperationalError (e.g. a missing table) is a failure.
        if timed_out:
            return None, ToolError(
                code="tool_timeout",
                message=f"{tool_name} took longer than {time_budget_seconds:g}s and was stopped. "
                "Try a shorter period or a smaller limit.",
            )
        return None, _failed(tool_name, exc)
    except ValueError:
        # Should not happen: the argument models check everything Period checks. Kept as a
        # safety net so a mismatch between the two still reaches the model as fixable input.
        return None, ToolError(code="invalid_arguments", message=f"Arguments for {tool_name} are invalid.")
    except Exception as exc:
        return None, _failed(tool_name, exc)
    finally:
        # Always removed: a leftover handler would keep aborting later queries on this connection.
        conn.set_progress_handler(None, _PROGRESS_EVERY_N_INSTRUCTIONS)


def _failed(tool_name: str, exc: Exception) -> ToolError:
    logger.error(
        "tool_failed",
        exc_info=(type(exc), exc, exc.__traceback__),
        extra={"event": "tool_failed", "tool": tool_name},
    )
    return ToolError(code="tool_failed", message=f"{tool_name} failed because of an internal error.")
