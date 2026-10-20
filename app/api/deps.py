"""FastAPI dependencies shared by the routers."""

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException

from app.config import Settings, get_settings
from app.db.connection import get_connection

SEED_HINT = "Database not found. Create it with: python -m scripts.seed_data"


def get_db(settings: Annotated[Settings, Depends(get_settings)]) -> Iterator[sqlite3.Connection]:
    """One read-only connection per request, always closed when the request ends.

    check_same_thread=False: FastAPI may run this dependency and the endpoint on different
    threadpool threads. That is safe because each connection belongs to exactly one
    request and is never shared.
    """
    # Checked up front for a clear 503; mode=ro would also refuse to create the file.
    if not settings.database_path.is_file():
        raise HTTPException(status_code=503, detail=SEED_HINT)
    conn = get_connection(settings.database_path, read_only=True, check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()
