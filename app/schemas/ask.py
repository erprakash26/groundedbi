"""Request and response models for POST /ask.

Units in the response follow the tools: money in evidence is integer cents plus whole-dollar
display strings; percentages are strings like "-17.2".
"""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.services.orchestrator import MAX_QUESTION_LENGTH, Status

# Stripped BEFORE the length check, so "   " fails as empty.
Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUESTION_LENGTH)]


class AskRequest(BaseModel):
    # strict: a number is rejected, not turned into the string "42".
    model_config = ConfigDict(extra="forbid", strict=True)

    question: Question = Field(description=f"A question about the sales data, 1-{MAX_QUESTION_LENGTH} characters.")


class GroundingSummary(BaseModel):
    passed: bool
    numbers_checked: int
    # No "unsupported" list: those numbers are unverified content and stay in the server logs.


class EvidenceOut(BaseModel):
    tool: str
    data: dict[str, Any]  # the tool result the answer's numbers were copied from


class TraceEntry(BaseModel):
    tool: str | None  # None if the planner named an unknown tool
    arguments: dict[str, Any] | None  # normalized; None if validation failed
    ok: bool
    error_code: str | None
    duration_ms: float


class UsageOut(BaseModel):
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float


class AskResponse(BaseModel):
    request_id: str | None  # same value as the X-Request-ID response header
    status: Status
    answer: str | None  # set for answered and out_of_scope
    clarifying_question: str | None  # set for clarification_needed
    message: str | None  # why there is no answer (grounding_failed, limits); null when answered
    grounding: GroundingSummary | None  # null when no final answer was produced
    evidence: list[EvidenceOut] | None  # only when status is "answered"; null otherwise
    trace: list[TraceEntry]
    duration_ms: float
    usage: UsageOut


class AskExample(BaseModel):
    """One example question for GET /ask/examples and the /docs dropdown."""

    summary: str  # short label, e.g. "What drove a drop"
    question: str
