"""TikTok video downloader via yt-dlp's dedicated (and actively maintained) extractor."""

from __future__ import annotations
from clipforge.core.config import DOWNLOADS_DIR
from clipforge.modules.common.ytdlp_base import DownloadError, VideoUnavailableError, YtDlpDownloaderBase


class TikTokDownloader(YtDlpDownloaderBase):
    platform_name = "tiktok"

    def __init__(self, downloads_base_dir=DOWNLOADS_DIR / "tiktok", cookies_path=None, download_archive_path=None):
        super().__init__(
            downloads_base_dir=downloads_base_dir,
            cookies_path=cookies_path,
            download_archive_path=download_archive_path,
        )

    def _classify_error(self, err_msg: str, url: str) -> DownloadError:
        if "video unavailable" in err_msg or "private" in err_msg or "removed" in err_msg:
            return VideoUnavailableError(f"TikTok video unavailable or private: {url}")
        return super()._classify_error(err_msg, url)
