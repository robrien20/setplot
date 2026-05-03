"""Runtime configuration loaded from env / .env via pydantic-settings.

Resolves the SetPlot data directory in this priority order:

1. ``SETPLOT_DATA_DIR`` env var (explicit override).
2. macOS: ``~/Library/Application Support/SetPlot/data``.
3. Linux/other: ``$XDG_DATA_HOME/setplot/data`` if XDG_DATA_HOME is set,
   otherwise ``~/.local/share/setplot/data``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _platform_data_dir() -> Path:
    """Return the OS-conventional SetPlot data dir (no env override)."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SetPlot" / "data"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "setplot" / "data"
    return Path.home() / ".local" / "share" / "setplot" / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    acr_host: str | None = None
    acr_access_key: str | None = None
    acr_access_secret: str | None = None
    audd_token: str | None = None

    setplot_data_dir: Path | None = None
    port: int = 8765

    def data_dir(self) -> Path:
        """Resolved data dir: explicit override or platform default."""
        return self.setplot_data_dir if self.setplot_data_dir else _platform_data_dir()


def get_settings() -> Settings:
    return Settings()
