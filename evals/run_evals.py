"""Run the evaluation questions through the planner and orchestrator and report metrics.

    python -m evals.run_evals [--split <name from evals/datasets.json>|all] [--fail-under RATE]

Seeds a fresh database in a temp folder (seed 42, default range), opens it read-only and calls
run_question with RuleBasedProvider directly, so the eval measures planning, tools and
grounding, not HTTP. Writes evals/results/latest.json (everything) and latest.md (report).

--fail-under gates only the dev split (exit code 1 if its pass rate is below RATE). The
held-out split is measured, never gated, so nobody is tempted to tune against it.
"""

import argparse
import json
import logging
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.db.connection import get_connection
from app.llm import RuleBasedProvider
from app.services.orchestrator import run_question
from evals.scoring import aggregate, aggregate_by, score_question, validate_questions
from scripts.seed_data import seed_database

EVALS_DIR = Path(__file__).resolve().parent
DATASETS_FILE = EVALS_DIR / "datasets.json"
RESULTS_DIR = EVALS_DIR / "results"
SEED = 42
SEED_START = "2026-01"
SEED_MONTHS = 6


def load_datasets(path: Path = DATASETS_FILE) -> dict[str, dict[str, Any]]:
    """split -> {"file": Path, "state": "dev"/"unseen"/"seen", "reason": str}, from evals/datasets.json."""
    entries = json.loads(path.read_text(encoding="utf-8"))["datasets"]
    return {
        e["split"]: {"file": path.parent / e["file"], "state": e["state"], "reason": e["reason"]} for e in entries
    }


DATASETS = load_datasets()
QUESTION_FILES = {split: info["file"] for split, info in DATASETS.items()}


def load_questions(path: Path) -> list[dict[str, Any]]:
    questions = json.loads(path.read_text(encoding="utf-8"))["questions"]
    validate_questions(questions)
    return questions


def git_info() -> dict[str, Any]:
    """Commit hash and whether the working tree had uncommitted changes when the eval ran."""
    def git(*args: str) -> str | None:
        try:
            done = subprocess.run(["git", *args], capture_output=True, text=True, cwd=EVALS_DIR, check=True)
        except (OSError, subprocess.CalledProcessError):
            return None
        return done.stdout.strip()

    commit = git("rev-parse", "--short", "HEAD")
    status = git("status", "--porcelain")
    return {"commit": commit or "unknown", "dirty": bool(status) if status is not None else None}


def run_questions(questions_by_split: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Seed a fresh database, run every question in file order, return the scored results."""
    provider = RuleBasedProvider()
    scored = []
    app_logger = logging.getLogger("app")
    previous_level = app_logger.level
    app_logger.setLevel(logging.WARNING)  # keep the console readable; warnings (grounding_failed) still show
    try:
        with tempfile.TemporaryDirectory(prefix="groundedbi-eval-") as folder:
            db_path = Path(folder) / "eval.db"
            seed_database(db_path, seed=SEED, start=SEED_START, months=SEED_MONTHS)
            conn = get_connection(db_path, read_only=True)
            try:
                for split, questions in questions_by_split.items():
                    for question in questions:
                        result = run_question(question["question"], conn, provider)
                        scored.append(score_question({**question, "split": split}, result.model_dump()))
            finally:
                conn.close()  # before the folder is removed: Windows cannot delete an open file
    finally:
        app_logger.setLevel(previous_level)
    return scored


def build_report(
    scored: list[dict[str, Any]], splits: list[str], states: dict[str, dict[str, str]] | None = None
) -> dict[str, Any]:
    by_split = {split: [s for s in scored if s["split"] == split] for split in splits}
    states = states or {}
    return {
        "meta": {
            **git_info(),
            "seed": SEED,
            "data_range": {"start": SEED_START, "months": SEED_MONTHS},
            "provider": RuleBasedProvider.name,
            "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
            "splits": splits,
            # Shown next to every split, so a "seen" held-out score is never presented as unseen.
            "dataset_states": {split: states.get(split, {"state": "unknown", "reason": ""}) for split in splits},
        },
        "metrics": {
            split: {"overall": aggregate(items), "by_category": aggregate_by(items, "category")}
            for split, items in by_split.items()
        },
        "questions": scored,
    }


# --- formatting ----------------------------------------------------------------------------


def _fmt_rate(metric: dict[str, Any]) -> str:
    if metric["total"] == 0:
        return "n/a"
    return f"{metric['rate'] * 100:.0f}% ({metric['passed']}/{metric['total']})"


def _fmt_ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"


_TABLE_HEADER = (
    "| Group | n | Pass | Status | Tools | Arguments | Grounding | Confident wrong | p50 ms | p95 ms | max ms | Avg tools |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|---|"
)


def _table_row(name: str, m: dict[str, Any]) -> str:
    lat = m["latency_ms"]
    return (
        f"| {name} | {m['questions']} | {_fmt_rate(m['pass'])} | {_fmt_rate(m['status_accuracy'])}"
        f" | {_fmt_rate(m['tool_accuracy'])} | {_fmt_rate(m['argument_accuracy'])}"
        f" | {_fmt_rate(m['grounding_pass_rate'])} | {m['confident_wrong']}"
        f" | {_fmt_ms(lat['p50'])} | {_fmt_ms(lat['p95'])} | {_fmt_ms(lat['max'])} | {m['avg_tool_calls']} |"
    )


def _json_inline(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _state_label(meta: dict[str, Any], split: str) -> str:
    info = meta["dataset_states"][split]
    return f"state: {info['state']}" + (f", {info['reason']}" if info["state"] == "seen" else "")


def render_markdown(report: dict[str, Any]) -> str:
    meta = report["meta"]
    dirty = " (uncommitted changes)" if meta["dirty"] else ""
    lines = [
        "# GroundedBI evaluation report",
        "",
        f"- Commit: `{meta['commit']}`{dirty}",
        f"- Seed: {meta['seed']} (data {meta['data_range']['start']}, {meta['data_range']['months']} months)",
        f"- Provider: {meta['provider']}",
        f"- Run at: {meta['timestamp']}",
        "",
        "Rates are over the questions where each check applies (shown as passed/total). "
        "Arguments are only checked when the tool list is correct; grounding only for answered questions. "
        "A confident wrong answer is an answered, grounded response to a question that still failed.",
    ]
    for split, metrics in report["metrics"].items():
        gate = "gated by --fail-under" if split == "dev" else "measured only, never gated"
        lines += [
            "",
            f"## Split: {split} ({_state_label(meta, split)}; {gate})",
            "",
            _TABLE_HEADER,
            _table_row("**overall**", metrics["overall"]),
        ]
        lines += [_table_row(category, m) for category, m in metrics["by_category"].items()]

    failures = [s for s in report["questions"] if not s["passed"]]
    lines += ["", f"## Failures ({len(failures)})"]
    if not failures:
        lines += ["", "None."]
    for item in failures:
        exp, act = item["expected"], item["actual"]
        flag = " **(confident wrong)**" if item["confident_wrong"] else ""
        lines += [
            "",
            f"### {item['id']} ({item['split']}, {item['category']}){flag}",
            "",
            f"- Question: {item['question']}",
            f"- Failed checks: {', '.join(item['failed_checks'])}",
            f"- Status: expected `{exp['status']}`, actual `{act['status']}`",
            f"- Tools: expected `{_json_inline(exp['tools'])}`, actual `{_json_inline(act['tools'])}`",
        ]
        if "arguments" in item["failed_checks"]:
            lines.append(f"- Arguments: expected `{_json_inline(exp['arguments'])}`, actual `{_json_inline(act['arguments'])}`")
        if item["missing_strings"]:
            lines.append(f"- Missing from text: `{_json_inline(item['missing_strings'])}`")
        if item["forbidden_strings_found"]:
            lines.append(f"- Forbidden strings found: `{_json_inline(item['forbidden_strings_found'])}`")
        text = act["text"].replace("\n", " / ")
        lines.append(f"- Returned text: {text}")
    return "\n".join(lines) + "\n"


def console_summary(report: dict[str, Any]) -> str:
    meta = report["meta"]
    out = [f"GroundedBI eval | commit {meta['commit']}{' (dirty)' if meta['dirty'] else ''} | seed {meta['seed']}"]
    for split, metrics in report["metrics"].items():
        m = metrics["overall"]
        lat = m["latency_ms"]
        out += [
            "",
            f"[{split}] {m['questions']} questions ({_state_label(meta, split)})",
            f"  pass            {_fmt_rate(m['pass'])}",
            f"  status          {_fmt_rate(m['status_accuracy'])}",
            f"  tools           {_fmt_rate(m['tool_accuracy'])}",
            f"  arguments       {_fmt_rate(m['argument_accuracy'])}",
            f"  grounding       {_fmt_rate(m['grounding_pass_rate'])}",
            f"  confident wrong {m['confident_wrong']}",
            f"  latency ms      p50 {_fmt_ms(lat['p50'])} | p95 {_fmt_ms(lat['p95'])} | max {_fmt_ms(lat['max'])}",
            f"  avg tool calls  {m['avg_tool_calls']}",
        ]
        for category, cm in metrics["by_category"].items():
            out.append(f"    {category:<24} {_fmt_rate(cm['pass'])}")
    failures = [s for s in report["questions"] if not s["passed"]]
    out += ["", f"Failures: {len(failures)}"]
    for item in failures:
        flag = "  [CONFIDENT WRONG]" if item["confident_wrong"] else ""
        out.append(f"  {item['id']:<12} {', '.join(item['failed_checks'])}{flag}")
    return "\n".join(out)


# --- entry point ---------------------------------------------------------------------------


def run(
    split: str = "all",
    fail_under: float | None = None,
    *,
    question_files: dict[str, Path] | None = None,
    results_dir: Path = RESULTS_DIR,
) -> int:
    """Run the eval, write the reports, print the summary. Returns the process exit code."""
    files = question_files or QUESTION_FILES
    splits = list(files) if split == "all" else [split]
    questions_by_split = {name: load_questions(files[name]) for name in splits}
    states = {name: {"state": info["state"], "reason": info["reason"]} for name, info in DATASETS.items()}
    report = build_report(run_questions(questions_by_split), splits, states)

    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "latest.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (results_dir / "latest.md").write_text(render_markdown(report), encoding="utf-8")
    print(console_summary(report))
    print(f"\nWrote {results_dir / 'latest.json'} and {results_dir / 'latest.md'}")

    if fail_under is None:
        return 0
    if "dev" not in report["metrics"]:
        print("Note: --fail-under only gates the dev split, which was not run; ignoring it.")
        return 0
    dev_rate = report["metrics"]["dev"]["overall"]["pass"]["rate"] or 0.0
    if dev_rate < fail_under:
        print(f"FAIL: dev pass rate {dev_rate:.2%} is below --fail-under {fail_under:.2%}")
        return 1
    print(f"OK: dev pass rate {dev_rate:.2%} meets --fail-under {fail_under:.2%}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # Any split registered in evals/datasets.json, or "all".
    parser.add_argument("--split", choices=[*DATASETS, "all"], default="all")
    parser.add_argument("--fail-under", type=float, default=None, metavar="RATE",
                        help="exit 1 if the dev pass rate is below RATE (0-1); held-out is never gated")
    args = parser.parse_args(argv)
    if args.fail_under is not None and not 0 <= args.fail_under <= 1:
        parser.error("--fail-under must be between 0 and 1")
    return run(args.split, args.fail_under)


if __name__ == "__main__":
    sys.exit(main())
