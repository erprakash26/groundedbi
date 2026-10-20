import sqlite3
from pathlib import Path


def get_connection(
    db_path: str | Path, *, read_only: bool = False, check_same_thread: bool = True
) -> sqlite3.Connection:
    """Open a SQLite connection with foreign keys enforced and rows accessible by column name.

    read_only=True opens the file with mode=ro: SQLite rejects every write, and a missing
    file raises sqlite3.OperationalError instead of being silently created empty.
    check_same_thread=False lets a thread other than the creator use the connection; only
    pass it when the connection is never shared between concurrent users.
    """
    if read_only:
        # as_uri() gives file:///D:/... on Windows and percent-encodes characters like ? and #.
        uri = Path(db_path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=check_same_thread)
    else:
        conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    # SQLite ignores FOREIGN KEY constraints unless this is enabled on every connection.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
