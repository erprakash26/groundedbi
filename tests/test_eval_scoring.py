import json
import shutil

import pytest

from evals import check_expectations, run_evals
from evals.scoring import aggregate, aggregate_by, check_arguments, percentile, score_question, validate_questions

DRIVERS_ARGS = {"period_a_start": "2026-02", "period_a_end": "2026-02", "period_b_start": "2026-03", "period_b_end": "2026-03"}


def make_question(**overrides):
    question = {
        "id": "t-01", "split": "dev", "category": "change_drivers", "question": "Why did revenue drop in March?",
        "expected_status": "answered", "expected_tools": ["get_change_drivers"], "expected_arguments": DRIVERS_ARGS,
        "must_contain": ["StudioBook 16 / West"], "must_not_contain": [], "notes": "",
    }  # fmt: skip
    return {**question, **overrides}


def make_result(**overrides):
    result = {
        "status": "answered",
        "answer": "Revenue fell by $314,032. The largest driver was StudioBook 16 / West.",
        "clarifying_question": None,
        "message": None,
        "grounding": {"passed": True, "numbers_checked": 1, "unsupported": []},
        "tool_calls": [
            {"tool": "get_data_overview", "arguments": {}},
            {"tool": "get_change_drivers", "arguments": {**DRIVERS_ARGS, "dimension": "product_region", "limit": 5}},
        ],
        "duration_ms": 12.5,
    }
    return {**result, **overrides}


# --- individual checks ------------------------------------------------------------------


def test_all_checks_pass():
    scored = score_question(make_question(), make_result())
    assert scored["passed"] is True
    assert scored["failed_checks"] == []
    assert scored["checks"] == {
        "status": True, "tools": True, "arguments": True,
        "must_contain": True, "must_not_contain": None, "grounding": True,
    }  # fmt: skip
    assert scored["tool_call_count"] == 2  # overview included in the count, excluded from tool matching


def test_wrong_status_fails():
    scored = score_question(make_question(expected_status="clarification_needed"), make_result())
    assert scored["checks"]["status"] is False and not scored["passed"]


def test_wrong_tool_fails_and_skips_the_argument_check():
    result = make_result(tool_calls=[{"tool": "compare_periods", "arguments": DRIVERS_ARGS}])
    scored = score_question(make_question(), result)
    assert scored["checks"]["tools"] is False
    assert scored["checks"]["arguments"] is None  # only checked when the tools are right


def test_no_tools_is_an_expectation_too():
    question = make_question(expected_tools=[], expected_arguments={}, must_contain=[])
    assert score_question(question, make_result())["checks"]["tools"] is False
    overview_only = make_result(tool_calls=[{"tool": "get_data_overview", "arguments": {}}])
    assert score_question(question, overview_only)["checks"]["tools"] is True


def test_wrong_argument_fails():
    wrong = make_result(tool_calls=[{"tool": "get_change_drivers", "arguments": {**DRIVERS_ARGS, "period_a_start": "2026-01"}}])
    assert score_question(make_question(), wrong)["checks"]["arguments"] is False


def test_unlisted_argument_keys_are_ignored():
    # dimension and limit are not in expected_arguments, so their values don't matter.
    calls = [{"tool": "get_change_drivers", "arguments": {**DRIVERS_ARGS, "dimension": "region", "limit": 3}}]
    assert check_arguments(DRIVERS_ARGS, calls) is True


def test_arguments_none_from_failed_validation_never_match():
    assert check_arguments({"limit": 5}, [{"tool": "get_top_products", "arguments": None}]) is False


def test_argument_list_is_one_dict_per_call():
    calls = [{"tool": "get_top_products", "arguments": {"start_month": "2026-01"}},
             {"tool": "get_top_products", "arguments": {"start_month": "2026-06"}}]  # fmt: skip
    assert check_arguments([{"start_month": "2026-01"}, {"start_month": "2026-06"}], calls) is True
    assert check_arguments([{"start_month": "2026-06"}, {"start_month": "2026-01"}], calls) is False  # order matters
    assert check_arguments([{"start_month": "2026-01"}], calls) is False  # wrong length


def test_must_contain_and_must_not_contain():
    scored = score_question(make_question(must_contain=["Phone Max"], must_not_contain=["$314,032"]), make_result())
    assert scored["checks"]["must_contain"] is False and scored["missing_strings"] == ["Phone Max"]
    assert scored["checks"]["must_not_contain"] is False and scored["forbidden_strings_found"] == ["$314,032"]


def test_text_falls_back_to_clarifying_question():
    question = make_question(expected_status="clarification_needed", expected_tools=[], expected_arguments={},
                             must_contain=["Northeast"])  # fmt: skip
    result = make_result(status="clarification_needed", answer=None, grounding=None,
                         clarifying_question='I couldn\'t find "Northeast" in the data.',
                         tool_calls=[{"tool": "get_data_overview", "arguments": {}}])  # fmt: skip
    scored = score_question(question, result)
    assert scored["passed"] is True
    assert scored["checks"]["grounding"] is None  # not an answer


def test_grounding_failure_fails_an_answered_question():
    scored = score_question(make_question(), make_result(grounding={"passed": False, "numbers_checked": 3}))
    assert scored["checks"]["grounding"] is False and not scored["passed"]


# --- confident wrong answers ---------------------------------------------------------------


def test_confident_wrong_is_answered_grounded_and_failed():
    wrong = make_result(answer="Revenue fell by $314,032. The largest driver was Phone Max / East.")
    scored = score_question(make_question(), wrong)
    assert not scored["passed"]
    assert scored["confident_wrong"] is True


def test_honest_failures_are_not_confident_wrong():
    # Wrong status (asked back instead of answering): a failure, but not a confident wrong answer.
    clarified = make_result(status="clarification_needed", answer=None, grounding=None, clarifying_question="Which?")
    assert score_question(make_question(), clarified)["confident_wrong"] is False
    # Answered but grounding failed: withheld, so not confident.
    ungrounded = make_result(grounding={"passed": False, "numbers_checked": 1})
    assert score_question(make_question(), ungrounded)["confident_wrong"] is False
    # Passing questions are never confident wrong.
    assert score_question(make_question(), make_result())["confident_wrong"] is False


# --- percentiles and aggregation ------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "p", "expected"),
    [
        ([], 50, None),
        ([7.0], 50, 7.0),
        ([7.0], 95, 7.0),
        ([1, 2, 3, 4], 50, 2),  # nearest rank: ceil(0.5 * 4) = 2nd value
        ([1, 2, 3, 4, 5], 50, 3),
        ([4, 1, 3, 2], 50, 2),  # unsorted input
        (list(range(1, 21)), 95, 19),  # ceil(0.95 * 20) = 19th value
        (list(range(1, 21)), 100, 20),
        (list(range(1, 11)), 95, 10),  # ceil(9.5) = 10th value
    ],
)
def test_percentile_nearest_rank(values, p, expected):
    assert percentile(values, p) == expected


def test_aggregate_rates_use_applicable_denominators():
    ok = score_question(make_question(), make_result(duration_ms=10.0))
    wrong_tool = score_question(make_question(id="t-02"), make_result(tool_calls=[], duration_ms=30.0))
    metrics = aggregate([ok, wrong_tool])
    assert metrics["pass"] == {"passed": 1, "total": 2, "rate": 0.5}
    assert metrics["tool_accuracy"] == {"passed": 1, "total": 2, "rate": 0.5}
    assert metrics["argument_accuracy"] == {"passed": 1, "total": 1, "rate": 1.0}  # skipped when tools were wrong
    assert metrics["latency_ms"] == {"p50": 10.0, "p95": 30.0, "max": 30.0}
    assert metrics["avg_tool_calls"] == 1.0  # (2 + 0) / 2


def test_aggregate_of_nothing_has_no_rates():
    metrics = aggregate([])
    assert metrics["pass"]["rate"] is None and metrics["latency_ms"]["p50"] is None


def test_aggregate_by_category():
    a = score_question(make_question(id="a", category="x"), make_result())
    b = score_question(make_question(id="b", category="y"), make_result(status="error"))
    c = score_question(make_question(id="c", category="x"), make_result())
    by_category = aggregate_by([a, b, c], "category")
    assert list(by_category) == ["x", "y"]
    assert by_category["x"]["pass"] == {"passed": 2, "total": 2, "rate": 1.0}
    assert by_category["y"]["pass"] == {"passed": 0, "total": 1, "rate": 0.0}


def test_validate_questions_rejects_missing_fields_and_duplicate_ids():
    with pytest.raises(ValueError, match="missing"):
        validate_questions([{"id": "x"}])
    with pytest.raises(ValueError, match="unique"):
        validate_questions([make_question(), make_question()])


def test_split_choices_come_from_datasets_json(capsys):
    # An unknown split fails during argument parsing, before anything runs; the error lists
    # every registered split, so a new dataset never needs a code change to be selectable.
    with pytest.raises(SystemExit):
        run_evals.main(["--split", "no_such_split"])
    error = capsys.readouterr().err
    for split in [*run_evals.DATASETS, "all"]:
        assert f"'{split}'" in error


def test_all_registered_question_files_are_valid():
    for path in run_evals.QUESTION_FILES.values():
        run_evals.load_questions(path)


# --- runner smoke test ------------------------------------------------------------------------


def _write_questions(path, questions):
    path.write_text(json.dumps({"questions": questions}), encoding="utf-8")


def _stable(report):
    """The report without the parts that legitimately change between runs."""
    report = json.loads(json.dumps(report))
    report["meta"].pop("timestamp")
    for item in report["questions"]:
        item.pop("duration_ms")
    for split in report["metrics"].values():
        for group in [split["overall"], *split["by_category"].values()]:
            group.pop("latency_ms")
    return report


@pytest.fixture
def two_question_file(tmp_path):
    path = tmp_path / "questions.json"
    _write_questions(path, [
        make_question(id="smoke-01", question="Why did revenue drop in March compared with February?",
                      must_contain=["StudioBook 16 / West", "$314,032"]),
        make_question(id="smoke-02", category="out_of_scope", question="What was our profit in March?",
                      expected_status="out_of_scope", expected_tools=[], expected_arguments={},
                      must_contain=[], must_not_contain=["$"]),
    ])  # fmt: skip
    return path


def test_runner_smoke_and_determinism(tmp_path, two_question_file, capsys):
    results = tmp_path / "results"
    code = run_evals.run("dev", question_files={"dev": two_question_file}, results_dir=results)
    assert code == 0
    first = json.loads((results / "latest.json").read_text(encoding="utf-8"))
    assert first["meta"]["seed"] == 42 and first["meta"]["commit"]
    assert first["metrics"]["dev"]["overall"]["pass"] == {"passed": 2, "total": 2, "rate": 1.0}
    markdown = (results / "latest.md").read_text(encoding="utf-8")
    assert "## Split: dev" in markdown and "Seed: 42" in markdown and "## Failures (0)" in markdown
    assert "[dev] 2 questions" in capsys.readouterr().out

    run_evals.run("dev", question_files={"dev": two_question_file}, results_dir=results)
    second = json.loads((results / "latest.json").read_text(encoding="utf-8"))
    assert _stable(first) == _stable(second)


def test_fail_under_gates_dev_only(tmp_path, two_question_file):
    failing = tmp_path / "failing.json"
    _write_questions(failing, [make_question(id="bad-01", question="What was our profit in March?")])  # expects answered
    files = {"dev": failing, "heldout": two_question_file}
    results = tmp_path / "results"
    assert run_evals.run("dev", 0.5, question_files=files, results_dir=results) == 1
    assert run_evals.run("dev", 0.0, question_files=files, results_dir=results) == 0
    # The held-out split never fails the run, even when its questions fail.
    assert run_evals.run("heldout", 1.0, question_files={"heldout": failing}, results_dir=results) == 0


def test_failures_are_listed_in_the_report(tmp_path):
    failing = tmp_path / "failing.json"
    _write_questions(failing, [make_question(id="bad-01", question="What was our profit in March?")])
    run_evals.run("dev", question_files={"dev": failing}, results_dir=tmp_path)
    markdown = (tmp_path / "latest.md").read_text(encoding="utf-8")
    assert "### bad-01 (dev, change_drivers)" in markdown
    assert "- Status: expected `answered`, actual `out_of_scope`" in markdown


# --- expectations checker -------------------------------------------------------------------------


def test_check_expectations_passes_on_the_current_files():
    assert check_expectations.main([]) == 0


def test_check_expectations_fails_on_a_wrong_value_and_never_writes(tmp_path, capsys):
    copy = tmp_path / "questions_dev.json"
    shutil.copy(run_evals.QUESTION_FILES["dev"], copy)
    doc = json.loads(copy.read_text(encoding="utf-8"))
    doc["questions"][0]["must_contain"] = ["StudioBook 16 / West", "$224,911"]  # off by one dollar
    copy.write_text(json.dumps(doc), encoding="utf-8")
    before = copy.read_bytes()

    assert check_expectations.main([str(copy)]) == 1
    assert "MISMATCH dev-01: $224,911" in capsys.readouterr().out
    assert copy.read_bytes() == before  # the checker only reads


def test_check_expectations_flags_numbers_without_tools(tmp_path):
    path = tmp_path / "q.json"
    _write_questions(path, [make_question(expected_tools=[], expected_arguments={}, must_contain=["$1,000"])])
    assert check_expectations.main([str(path)]) == 1


def test_expected_numbers_extracts_money_and_percentages():
    question = make_question(must_contain=["StudioBook 16 / West", "$224,910", "71.6%", "Q2 2026", "-$8,792"])
    assert check_expectations.expected_numbers(question) == ["$224,910", "71.6", "-$8,792"]
