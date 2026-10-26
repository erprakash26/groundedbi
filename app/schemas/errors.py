"""The error envelope every failure returns, documented in OpenAPI instead of FastAPI's default.

    {"error": {"code": "...", "message": "...", "request_id": "...", "details": [...]}}

Built by app/api/errors.py. Input values are never echoed back, so there is no "input" field.
"""

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    location: str | None  # "query" or "body"
    field: str  # e.g. "start" or "question"; "<unrecognized field>" for an unsafe extra field name
    message: str
    type: str  # e.g. "missing", "string_too_long", "extra_forbidden"


class ErrorBody(BaseModel):
    code: str  # stable, machine-readable: validation_error, internal_error, database_unavailable, ...
    message: str
    request_id: str | None  # same as the X-Request-ID response header
    details: list[ErrorDetail] | None = None  # only for validation errors


class ErrorResponse(BaseModel):
    error: ErrorBody
