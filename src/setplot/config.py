"""Runtime configuration loaded from env / .env via pydantic-settings.

Phase 1 just exposes the ACR creds and a few server knobs. Phase 2 adds the
data-dir resolution; Phase 3 wires server/SSE settings.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    acr_host: str | None = None
    acr_access_key: str | None = None
    acr_access_secret: str | None = None
    audd_token: str | None = None

    setplot_data_dir: Path | None = None
    port: int = 8765


def get_settings() -> Settings:
    return Settings()
