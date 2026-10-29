"""Deployment setup: requirements, Python version, Vercel build, CI, and a missing database at startup."""

import json
import re
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import pytest
import yaml  # PyYAML comes with uvicorn[standard]
from fastapi.testclient import TestClient

from app.config import PROJECT_ROOT, Settings, get_settings
from app.main import create_app
from scripts import seed_data

ROOT = Path(__file__).resolve().parent.parent
BUILD_COMMAND = "python -m scripts.seed_data --db data/groundedbi.db"
RUNTIME_PACKAGES = {"fastapi", "uvicorn[standard]", "pydantic", "pydantic-settings"}
DEV_PACKAGES = {"pytest", "httpx2", "pip-audit"}


def _requirements(name: str) -> list[str]:
    lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def _pins(lines: list[str]) -> dict[str, str]:
    pins = {}
    for line in lines:
        if line.startswith("-r "):
            continue
        name, _, pinned = line.partition("==")
        assert pinned and re.fullmatch(r"[0-9][0-9A-Za-z.]*", pinned), f"not pinned exactly: {line}"
        pins[name] = pinned
    return pins


# --- requirements ---


def test_runtime_requirements_are_exactly_the_four_runtime_packages_pinned():
    assert set(_pins(_requirements("requirements.txt"))) == RUNTIME_PACKAGES


def test_dev_requirements_include_runtime_and_pin_the_tools():
    lines = _requirements("requirements-dev.txt")
    assert lines[0] == "-r requirements.txt"
    assert set(_pins(lines)) == DEV_PACKAGES


def test_pins_match_the_installed_versions_the_tests_run_on():
    pins = _pins(_requirements("requirements.txt")) | _pins(_requirements("requirements-dev.txt"))
    for name, pinned in pins.items():
        assert version(name.split("[")[0]) == pinned, name


# --- Python version ---


def test_python_version_file_asks_vercel_for_3_12():
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.12"


# --- Vercel ---


def _vercel() -> dict:
    return json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))


def test_vercel_build_command_seeds_the_database_the_app_reads():
    assert _vercel()["buildCommand"] == BUILD_COMMAND
    db_argument = BUILD_COMMAND.split("--db ")[1]
    # Vercel runs the build in the project root, so the relative path lands on the app's default.
    # The coded default (not Settings(), which a DATABASE_PATH environment variable would change).
    assert PROJECT_ROOT / db_argument == Settings.model_fields["database_path"].default


def test_vercel_bundle_excludes_tests_evals_and_docs_for_the_real_entrypoint():
    functions = _vercel()["functions"]
    assert list(functions) == ["app/main.py"]
    assert (ROOT / "app" / "main.py").is_file()
    assert functions["app/main.py"]["excludeFiles"] == "{tests/**,evals/**,docs/**}"


def test_no_pyproject_toml_to_shadow_requirements_txt():
    # Vercel's installer prefers pyproject.toml over requirements.txt when both exist.
    assert not (ROOT / "pyproject.toml").exists()


def test_build_command_runs_with_the_standard_library_only(tmp_path):
    # -S skips site-packages: if the seed script imported any third-party package, this fails.
    db_path = tmp_path / "data" / "groundedbi.db"
    args = BUILD_COMMAND.split()[1:-1] + [str(db_path)]  # same module and flag, a temp target
    completed = subprocess.run(
        [sys.executable, "-S", *args], cwd=ROOT, capture_output=True, text=True, timeout=120
    )
    assert completed.returncode == 0, completed.stderr
    assert db_path.is_file()


def test_importing_the_seed_script_does_not_import_third_party_packages():
    code = "import sys, scripts.seed_data; print(sorted(m for m in sys.modules if m.split('.')[0] in {names!r}))"
    names = {"pydantic_settings", "pydantic", "fastapi", "starlette"}
    completed = subprocess.run(
        [sys.executable, "-c", code.format(names=names)], cwd=ROOT, capture_output=True, text=True, timeout=60
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "[]"


# --- the seed script's default path ---


@pytest.fixture
def database_path_env(monkeypatch, tmp_path):
    """Point DATABASE_PATH at a temp file for get_settings(), and restore the cached settings after."""
    path = tmp_path / "from-env" / "groundedbi.db"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


def test_seed_default_path_is_the_apps_database_path(database_path_env):
    assert seed_data.default_db_path() == get_settings().database_path == database_path_env


def test_seeding_without_db_fills_the_database_the_app_reads(database_path_env, capsys):
    seed_data.main(["--months", "4"])
    assert database_path_env.is_file()


def test_seeding_with_db_ignores_the_app_setting(database_path_env, tmp_path, capsys):
    target = tmp_path / "explicit.db"
    seed_data.main(["--db", str(target), "--months", "4"])
    assert target.is_file()
    assert not database_path_env.exists()


# --- missing database at startup ---


def _app_with_database(path: Path):
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(database_path=path, _env_file=None)
    return app


def test_missing_database_at_startup_is_logged_then_503_and_never_created(tmp_path, log_lines):
    missing = tmp_path / "data" / "groundedbi.db"
    with TestClient(_app_with_database(missing)) as client:  # "with" runs the lifespan
        startup = [line for line in log_lines() if line.get("event") == "database_unavailable"]
        assert len(startup) == 1
        assert startup[0]["level"] == "ERROR"
        assert startup[0]["database_path"] == str(missing)
        assert "scripts.seed_data" in startup[0]["hint"]

        response = client.get("/analytics/meta")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "database_unavailable"
        assert client.get("/").status_code == 200  # the pages still work
    assert not missing.exists()
    assert not missing.parent.exists()


def test_present_database_logs_no_startup_error(tmp_path, log_lines):
    path = tmp_path / "groundedbi.db"
    seed_data.seed_database(path, seed=42, start="2026-01", months=4)
    with TestClient(_app_with_database(path)) as client:
        assert client.get("/analytics/meta").status_code == 200
    assert not [line for line in log_lines() if line.get("event") == "database_unavailable"]


# --- CI ---


def test_ci_runs_every_check_on_3_11_and_3_12():
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))  # YAML 1.1 reads a bare "on" key as True
    assert set(triggers) == {"push", "pull_request"}
    job = workflow["jobs"]["test"]
    assert job["strategy"]["matrix"]["python-version"] == ["3.11", "3.12"]
    commands = [step["run"] for step in job["steps"] if "run" in step]
    expected = [
        "python -m pip install -r requirements-dev.txt",
        "pip-audit",
        "python -m pytest -v",
        "python -m evals.check_expectations",
        "python -m evals.run_evals --split dev --fail-under 1.0",
    ]
    joined = "\n".join(commands)
    positions = [joined.index(command) for command in expected]
    assert positions == sorted(positions)  # all present, in this order
