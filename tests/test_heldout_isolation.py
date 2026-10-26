"""Guard: held-out question text must never leak into the application code.

If any 4 consecutive words of a held-out question appear in an app/ source file (comments
and strings included), the planner may have been tuned to that question.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HELDOUT = ROOT / "evals" / "questions_heldout.json"
APP_FILES = sorted((ROOT / "app").rglob("*.py"))
_WORD = re.compile(r"[a-z0-9]+")


def words(text: str) -> list[str]:
    """Lowercase words without punctuation: "How's  it" -> ["how", "s", "it"]."""
    return _WORD.findall(text.lower())


def four_grams(text: str) -> set[str]:
    tokens = words(text)
    return {" ".join(tokens[i : i + 4]) for i in range(len(tokens) - 3)}


def heldout_four_grams() -> dict[str, str]:
    questions = json.loads(HELDOUT.read_text(encoding="utf-8"))["questions"]
    return {gram: q["id"] for q in questions for gram in four_grams(q["question"])}


def test_the_guard_sees_the_heldout_questions_and_the_app_files():
    assert len(heldout_four_grams()) > 20
    assert any(path.name == "rule_based.py" for path in APP_FILES)


def test_four_grams_normalize_case_and_punctuation():
    assert four_grams("Why, did REVENUE drop?") == {"why did revenue drop"}
    assert four_grams("too short") == set()


@pytest.mark.parametrize("path", APP_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_heldout_four_word_sequence_in_app_source(path):
    source = " " + " ".join(words(path.read_text(encoding="utf-8"))) + " "
    leaked = sorted(f"{gram!r} ({qid})" for gram, qid in heldout_four_grams().items() if f" {gram} " in source)
    assert not leaked, f"held-out text found in {path.name}: {leaked}"


def test_the_guard_catches_a_leak(tmp_path):
    gram, _ = next(iter(heldout_four_grams().items()))
    leaky = tmp_path / "leaky.py"
    leaky.write_text(f'PATTERN = "{gram}"\n', encoding="utf-8")
    source = " " + " ".join(words(leaky.read_text(encoding="utf-8"))) + " "
    assert f" {gram} " in source
