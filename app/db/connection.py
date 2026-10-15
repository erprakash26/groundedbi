import sqlite3
from pathlib import Path


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with foreign keys enforced and rows accessible by column name."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # SQLite ignores FOREIGN KEY constraints unless this is enabled on every connection.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
