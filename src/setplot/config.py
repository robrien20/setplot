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

    # Streaming integrations (all optional). The viewer hides export buttons
    # for services whose credentials aren't configured.
    spotify_client_id: str | None = None
    spotify_redirect_uri: str = "http://127.0.0.1:8765/auth/spotify/callback"

    apple_music_team_id: str | None = None
    apple_music_key_id: str | None = None
    # Path to the .p8 private key file downloaded from the Apple Developer portal.
    apple_music_key_path: Path | None = None

    def data_dir(self) -> Path:
        """Resolved data dir: explicit override or platform default."""
        return self.setplot_data_dir if self.setplot_data_dir else _platform_data_dir()

    def spotify_enabled(self) -> bool:
        return bool(self.spotify_client_id)

    def apple_music_enabled(self) -> bool:
        return bool(
            self.apple_music_team_id
            and self.apple_music_key_id
            and self.apple_music_key_path
            and self.apple_music_key_path.exists()
        )


def get_settings() -> Settings:
    return Settings()
