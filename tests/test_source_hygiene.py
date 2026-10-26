"""Guard: no source file may contain a backspace character (\\x08).

A regex word boundary written as "\\b" in a non-raw string becomes a backspace, which is
invisible in an editor and silently breaks the pattern (it happened once in question_parser.py).
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SOURCE_FILES = sorted([*(ROOT / "app").rglob("*.py"), *(ROOT / "evals").rglob("*.py")])
BACKSPACE = "\x08"


def test_the_guard_scans_app_and_evals():
    names = {path.name for path in SOURCE_FILES}
    assert {"question_parser.py", "run_evals.py"} <= names


@pytest.mark.parametrize("path", SOURCE_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_backspace_characters(path):
    text = path.read_text(encoding="utf-8")
    lines = [number for number, line in enumerate(text.splitlines(), start=1) if BACKSPACE in line]
    assert not lines, f"{path.relative_to(ROOT)} contains a backspace (\\x08) on line(s) {lines}: a '\\b' in a non-raw string?"


def test_the_guard_catches_a_backspace(tmp_path):
    broken = tmp_path / "broken.py"
    broken.write_text('PATTERN = "\\bword\\b"\n'.replace("\\b", BACKSPACE), encoding="utf-8")
    assert BACKSPACE in broken.read_text(encoding="utf-8")
