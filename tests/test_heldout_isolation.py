"""Guard: held-out question text must never leak into the application code.

Every question file in evals/datasets.json whose state is not "dev" is held out. If any 4
consecutive words of one of its questions appear in an app/ source file (comments and strings
included), the planner may have been tuned to that question.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DATASETS = ROOT / "evals" / "datasets.json"
APP_FILES = sorted((ROOT / "app").rglob("*.py"))
_WORD = re.compile(r"[a-z0-9]+")


def heldout_files() -> list[Path]:
    entries = json.loads(DATASETS.read_text(encoding="utf-8"))["datasets"]
    return [DATASETS.parent / entry["file"] for entry in entries if entry["state"] != "dev"]


def words(text: str) -> list[str]:
    """Lowercase words without punctuation: "How's  it" -> ["how", "s", "it"]."""
    return _WORD.findall(text.lower())


def four_grams(text: str) -> set[str]:
    tokens = words(text)
    return {" ".join(tokens[i : i + 4]) for i in range(len(tokens) - 3)}


def heldout_four_grams() -> dict[str, str]:
    grams: dict[str, str] = {}
    for path in heldout_files():
        for question in json.loads(path.read_text(encoding="utf-8"))["questions"]:
            for gram in four_grams(question["question"]):
                grams.setdefault(gram, question["id"])
    return grams


def test_every_non_dev_dataset_is_guarded():
    names = {path.name for path in heldout_files()}
    assert {"questions_heldout.json", "questions_heldout_v2.json"} <= names
    assert "questions_dev.json" not in names
    assert all(path.is_file() for path in heldout_files())


def test_the_guard_sees_the_heldout_questions_and_the_app_files():
    ids = set(heldout_four_grams().values())
    assert any(i.startswith("heldout-v2-") for i in ids) and any(i.startswith("heldout-0") for i in ids)
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
