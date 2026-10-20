from pathlib import Path

from app.config import PROJECT_ROOT, Settings, get_settings


def test_project_root_contains_the_app_package():
    assert (PROJECT_ROOT / "app" / "config.py").is_file()


def test_default_database_path_is_under_project_root(monkeypatch):
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    # _env_file=None: ignore a developer's local .env so the test sees the code default.
    assert Settings(_env_file=None).database_path == PROJECT_ROOT / "data" / "groundedbi.db"


def test_relative_env_path_resolves_against_project_root_not_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # a working directory far from the project
    monkeypatch.setenv("DATABASE_PATH", "custom/other.db")
    path = Settings(_env_file=None).database_path
    assert path == PROJECT_ROOT / "custom" / "other.db"
    assert path.is_absolute()


def test_absolute_env_path_is_kept(monkeypatch, tmp_path):
    absolute = tmp_path / "elsewhere.db"
    monkeypatch.setenv("DATABASE_PATH", str(absolute))
    assert Settings(_env_file=None).database_path == absolute


def test_get_settings_is_cached():
    assert get_settings() is get_settings()
    assert isinstance(get_settings().database_path, Path)
