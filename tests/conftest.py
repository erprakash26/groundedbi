import io
import json
import logging

import pytest

from app.db.connection import get_connection
from app.observability.logging import JsonFormatter
from scripts.seed_data import SCHEMA_PATH, seed_database


@pytest.fixture
def log_lines():
    """Capture log output exactly as production formats it; call the fixture to get parsed lines."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    def parsed() -> list[dict]:
        # json.loads on every line also proves each line is one valid JSON object.
        return [json.loads(line) for line in stream.getvalue().splitlines()]

    yield parsed
    root.removeHandler(handler)
    root.setLevel(old_level)


@pytest.fixture(scope="module")
def seeded_conn(tmp_path_factory):
    """One seeded database per test module (seed 42, 2026-01 to 2026-06). Tests only read from it."""
    db_path = tmp_path_factory.mktemp("seeded") / "test.db"
    seed_database(db_path, seed=42, start="2026-01", months=6)
    conn = get_connection(db_path)
    yield conn
    conn.close()


def _schema_only_conn(db_path):
    conn = get_connection(db_path)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn


@pytest.fixture
def empty_conn(tmp_path):
    """A database with the schema but no rows."""
    conn = _schema_only_conn(tmp_path / "empty.db")
    yield conn
    conn.close()


@pytest.fixture
def tiny_conn(tmp_path):
    """Hand-made data with known answers.

    Alpha sells only in January (2,000 cents). Beta sells on the last day of January
    (500 cents) and the first day of February (1,500 cents).
    """
    conn = _schema_only_conn(tmp_path / "tiny.db")
    with conn:
        conn.executemany(
            "INSERT INTO products (product_id, name, category, unit_price_cents) VALUES (?, ?, ?, ?)",
            [(1, "Alpha", "Gadgets", 1000), (2, "Beta", "Gadgets", 500)],
        )
        conn.execute("INSERT INTO regions (region_id, name) VALUES (?, ?)", (1, "North"))
        conn.executemany(
            "INSERT INTO sales (sale_date, product_id, region_id, quantity, unit_price_cents, revenue_cents) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("2026-01-15", 1, 1, 2, 1000, 2000),
                ("2026-01-31", 2, 1, 1, 500, 500),
                ("2026-02-01", 2, 1, 3, 500, 1500),
            ],
        )
    yield conn
    conn.close()
