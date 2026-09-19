"""Global configuration and paths for Clip Forge V2."""

from __future__ import annotations
import os
import sys
from pathlib import Path

# Base Paths
if getattr(sys, "frozen", False):
    exe_dir = Path(sys.executable).parent
    # If running from within a repo's dist folder, resolve to the repo root
    if exe_dir.name.lower() == "dist" and (exe_dir.parent / "pyproject.toml").exists():
        PROJECT_ROOT = exe_dir.parent
    elif (exe_dir / "pyproject.toml").exists():
        PROJECT_ROOT = exe_dir
    else:
        PROJECT_ROOT = exe_dir
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

DATA_DIR = PROJECT_ROOT / "data"
DATABASE_PATH = DATA_DIR / "clipforge.db"

DOWNLOADS_DIR = DATA_DIR / "downloads"
DOWNLOAD_ARCHIVES_DIR = DATA_DIR / "download_archives"
RENDERS_DIR = DATA_DIR / "renders"
CREDENTIALS_DIR = DATA_DIR / "credentials"
SESSIONS_DIR = DATA_DIR / "sessions"
LOGS_DIR = DATA_DIR / "logs"

# YouTube OAuth defaults
DEFAULT_YOUTUBE_CLIENT_SECRET_FILE = CREDENTIALS_DIR / "youtube_client_secret.json"
DEFAULT_YOUTUBE_TOKEN_FILE = CREDENTIALS_DIR / "youtube_token.json"
DEFAULT_YOUTUBE_COOKIES_FILE = SESSIONS_DIR / "youtube_cookies.txt"

# yt-dlp --download-archive files, um por plataforma (camada extra de
# deduplicação por ID de vídeo, além da checagem por source_url no banco).
DOWNLOAD_ARCHIVE_FILES = {
    "youtube": DOWNLOAD_ARCHIVES_DIR / "youtube.txt",
    "tiktok": DOWNLOAD_ARCHIVES_DIR / "tiktok.txt",
    "instagram": DOWNLOAD_ARCHIVES_DIR / "instagram.txt",
}

# Ensure all critical runtime directories exist
def init_directories() -> None:
    """Ensure all required data directories exist."""
    for path in (
        DATA_DIR,
        DOWNLOADS_DIR,
        DOWNLOADS_DIR / "youtube",
        DOWNLOADS_DIR / "tiktok",
        DOWNLOADS_DIR / "instagram",
        DOWNLOAD_ARCHIVES_DIR,
        RENDERS_DIR,
        CREDENTIALS_DIR,
        SESSIONS_DIR,
        LOGS_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


# Auto-initialize directories on import
init_directories()
