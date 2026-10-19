"""application data paths shared by history ffmpeg and settings."""

from __future__ import annotations

import os
import platform
from pathlib import Path

# keep in sync with app/src-tauri/tauri.conf.json identifier
app_data_name = "io.github.william563214.unified-downloader"
db_filename = "udl.sqlite3"
udl_db_env = "UDL_DB"
udl_data_env = "UDL_DATA_DIR"


def app_data_dir(explicit: str | Path | None = None) -> Path:
    """return the platform app data directory for unified-downloader."""
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get(udl_data_env)
    if env:
        return Path(env).expanduser().resolve()
    if platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support" / app_data_name
    elif platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        base = Path(base) / app_data_name
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
        base = Path(base) / app_data_name
    return base.resolve()


def default_db_path(explicit: str | Path | None = None) -> Path:
    """SQLite path under app data (or UDL_DB override)."""
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get(udl_db_env)
    if env:
        return Path(env).expanduser().resolve()
    return app_data_dir() / db_filename
