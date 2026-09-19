"""YouTube video downloader — thin specialization of the shared yt-dlp base."""

from __future__ import annotations
from clipforge.core.config import DOWNLOADS_DIR
from clipforge.modules.common.ytdlp_base import (
    AgeRestrictedError,
    DownloadError,
    VideoUnavailableError,
    YtDlpDownloaderBase,
)

# Backwards-compatible alias: this exception name predates the multi-platform
# generalization and is kept so existing imports/tests keep working.
YouTubeDownloadError = DownloadError

__all__ = [
    "YouTubeDownloader",
    "YouTubeDownloadError",
    "AgeRestrictedError",
    "VideoUnavailableError",
]


class YouTubeDownloader(YtDlpDownloaderBase):
    """
    Downloads YouTube videos (own channel or third-party, shorts, long-form,
    and live VODs) via yt-dlp. No OAuth required for download — yt-dlp works
    against any public channel without spending YouTube Data API quota.
    """

    platform_name = "youtube"

    def __init__(self, downloads_base_dir=DOWNLOADS_DIR / "youtube", cookies_path=None, download_archive_path=None):
        super().__init__(
            downloads_base_dir=downloads_base_dir,
            cookies_path=cookies_path,
            download_archive_path=download_archive_path,
        )
