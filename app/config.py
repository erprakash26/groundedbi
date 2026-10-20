"""Application settings, read from environment variables (and .env) by pydantic-settings."""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# app/config.py -> app -> project root. Paths are anchored here, not to the current
# working directory, so the app finds its data however and wherever it is started.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # extra="ignore": .env also holds keys this class doesn't model yet (ANTHROPIC_API_KEY).
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    # Env var DATABASE_PATH (matching is case-insensitive). A relative value is resolved
    # against the project root, e.g. "data/other.db" -> <project root>/data/other.db.
    database_path: Path = PROJECT_ROOT / "data" / "groundedbi.db"

    @field_validator("database_path")
    @classmethod
    def _anchor_to_project_root(cls, value: Path) -> Path:
        return value if value.is_absolute() else PROJECT_ROOT / value


@lru_cache
def get_settings() -> Settings:
    """One Settings instance per process. Request code gets it via Depends(get_settings)
    only, so tests can swap it with app.dependency_overrides."""
    return Settings()
