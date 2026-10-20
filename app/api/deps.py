"""FastAPI dependencies shared by the routers."""

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends

from app.api.errors import APIError
from app.config import Settings, get_settings
from app.db.connection import get_connection

SEED_HINT = "Database not found or not seeded. Create it with: python -m scripts.seed_data"


def _database_unavailable() -> APIError:
    return APIError(503, "database_unavailable", SEED_HINT)


def get_db(settings: Annotated[Settings, Depends(get_settings)]) -> Iterator[sqlite3.Connection]:
    """One read-only connection per request, always closed when the request ends.

    check_same_thread=False: FastAPI may run this dependency and the endpoint on different
    threadpool threads. That is safe because each connection belongs to exactly one
    request and is never shared.
    """
    # Checked up front for a clear 503; mode=ro would also refuse to create the file.
    if not settings.database_path.is_file():
        raise _database_unavailable()
    conn = get_connection(settings.database_path, read_only=True, check_same_thread=False)
    try:
        # A file that exists but was never seeded (e.g. empty) has no tables: 503, not a 500
        # later from "no such table".
        has_sales = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = ? AND name = ?", ("table", "sales")
        ).fetchone()
        if has_sales is None:
            raise _database_unavailable()
        yield conn
    finally:
        conn.close()
