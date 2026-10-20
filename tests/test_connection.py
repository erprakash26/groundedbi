import sqlite3
import threading

import pytest

from app.db.connection import get_connection
from scripts.seed_data import seed_database


@pytest.fixture(scope="module")
def seeded_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("conn") / "conn test.db"  # space in the name exercises URI encoding
    seed_database(path, seed=42, start="2026-01", months=4)
    return path


def test_read_only_connection_can_read(seeded_path):
    conn = get_connection(seeded_path, read_only=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] > 0
    finally:
        conn.close()


def test_read_only_connection_rejects_writes(seeded_path):
    conn = get_connection(seeded_path, read_only=True)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("INSERT INTO regions (name) VALUES ('Nowhere')")
    finally:
        conn.close()


def test_read_only_connection_never_creates_a_missing_file(tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        get_connection(missing, read_only=True)
    assert not missing.exists()


def test_check_same_thread_false_allows_use_from_another_thread(seeded_path):
    conn = get_connection(seeded_path, read_only=True, check_same_thread=False)
    errors = []

    def use_connection():
        try:
            conn.execute("SELECT 1").fetchone()
        except sqlite3.Error as exc:
            errors.append(exc)

    worker = threading.Thread(target=use_connection)
    worker.start()
    worker.join()
    conn.close()
    assert errors == []


def test_default_connection_is_writable_and_enforces_foreign_keys(tmp_path):
    conn = get_connection(tmp_path / "new.db")
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        conn.execute("CREATE TABLE t (x INTEGER)")
    finally:
        conn.close()
