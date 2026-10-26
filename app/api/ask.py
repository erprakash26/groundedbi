"""POST /ask: a question in, a grounded answer (or a clarifying question / refusal) out.

Thin on purpose: validate the body, call run_question, map the result. All decisions
(planning, limits, grounding, evidence) live in app/services/orchestrator.py.

HTTP status:
  200  answered, clarification_needed, out_of_scope, grounding_failed, limit_exceeded,
       tool_call_limit_exceeded: the request was handled; "status" in the body says how
  422  invalid body (existing validation envelope; the question is never echoed back)
  500  provider_error or error (existing internal_error envelope, generic message)
  503  database missing or not seeded (from get_db)
"""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Request
from fastapi.openapi.models import Example

from app.api.deps import get_db, get_provider
from app.api.errors import APIError
from app.llm import Provider
from app.schemas.ask import AskRequest, AskResponse, EvidenceOut, GroundingSummary, TraceEntry, UsageOut
from app.services.orchestrator import run_question

router = APIRouter(tags=["ask"])

# Shown as a dropdown in /docs, ready for "Try it out".
_EXAMPLES = {
    "drivers": Example(summary="What drove a drop", value={"question": "Why did revenue drop in March compared with February?"}),
    "clarify": Example(summary="Unknown region (asks back)", value={"question": "Revenue in Europe in March"}),
    "out_of_scope": Example(summary="Metric not in the data", value={"question": "What was our profit in March?"}),
    "false_premise": Example(summary="Wrong premise (corrected)", value={"question": "Why did revenue drop from May to June?"}),
}
_FAILED = ("provider_error", "error")


@router.post("/ask", response_model=AskResponse)
def ask(
    request: Request,
    body: Annotated[AskRequest, Body(openapi_examples=_EXAMPLES)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    provider: Annotated[Provider, Depends(get_provider)],
) -> AskResponse:
    """Answer a question about the sales data, with the evidence and a trace of every tool call."""
    try:
        result = run_question(body.question, conn, provider)
    except ValueError:
        # AskRequest already enforces the same rules; this only guards against the two drifting apart.
        raise APIError(422, "validation_error", "Request validation failed.") from None
    if result.status in _FAILED:
        # The traceback was logged by the orchestrator; the client gets the generic envelope.
        raise APIError(500, "internal_error", "An internal error occurred.")

    return AskResponse(
        request_id=getattr(request.state, "request_id", None),
        status=result.status,
        answer=result.answer,
        clarifying_question=result.clarifying_question,
        message=result.message,
        grounding=(
            GroundingSummary(passed=result.grounding.passed, numbers_checked=result.grounding.numbers_checked)
            if result.grounding
            else None
        ),
        evidence=(
            [EvidenceOut(tool=item.tool, data=item.data) for item in result.evidence]
            if result.status == "answered"
            else None
        ),
        trace=[
            TraceEntry(tool=t.tool, arguments=t.arguments, ok=t.ok, error_code=t.error_code, duration_ms=t.duration_ms)
            for t in result.tool_calls
        ],
        duration_ms=result.duration_ms,
        usage=UsageOut(**result.usage.model_dump()),
    )
