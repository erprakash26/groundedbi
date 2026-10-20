"""Structured JSON logging with the standard library only.

Every log line is one JSON object, e.g.
  {"timestamp": "2026-10-20T09:15:02.123+00:00", "level": "INFO", "logger": "app.access",
   "message": "request_completed", "request_id": "3f2a...", "event": "request_completed", ...}

Running the server:
  uvicorn app.main:app
configure_logging() disables uvicorn's own access logger, so each request is logged once,
as JSON, by our middleware. No --no-access-log flag is needed: doing it in code means the
output is the same however the server is started, and nobody can forget the flag.
uvicorn's startup/shutdown messages (logger "uvicorn.error") stay in uvicorn's plain format.
"""

import json
import logging
import sys
from datetime import UTC, datetime

from app.observability.context import get_request_id

# Attributes every LogRecord has. Anything else on a record came from extra={...}.
_STANDARD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime", "taskName"}
_HANDLER_NAME = "groundedbi-json"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            # An explicit extra={"request_id": ...} wins; otherwise use the current request's ID.
            "request_id": getattr(record, "request_id", None) or get_request_id(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS and key not in entry:
                entry[key] = value
        if record.exc_info:
            entry["traceback"] = self.formatException(record.exc_info)
        # default=str: an unexpected non-JSON value (a Path, a Decimal) must not break logging.
        return json.dumps(entry, default=str, ensure_ascii=False)


def configure_logging(level: int | str = logging.INFO) -> None:
    """Send all app logs to stderr as JSON. Safe to call more than once."""
    root = logging.getLogger()
    root.setLevel(level)
    # Named handler: a second call finds it and does not add a duplicate.
    if not any(handler.get_name() == _HANDLER_NAME for handler in root.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.set_name(_HANDLER_NAME)
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
    # Our middleware writes the access log; uvicorn's would be a second, plain-text line.
    logging.getLogger("uvicorn.access").disabled = True
