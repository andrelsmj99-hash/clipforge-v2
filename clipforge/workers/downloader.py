"""Downloader worker process consuming download jobs (multi-platform)."""

from __future__ import annotations
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from clipforge.core.config import (
    DEFAULT_YOUTUBE_COOKIES_FILE,
    DOWNLOAD_ARCHIVE_FILES,
    DOWNLOADS_DIR,
)
from clipforge.core.db import Database
from clipforge.core.models import Job, JobType, Platform, Video, VideoKind, VideoStatus
from clipforge.core.queue import JobQueue
from clipforge.modules.instagram.downloader import InstagramDownloader
from clipforge.modules.tiktok.downloader import TikTokDownloader
from clipforge.modules.youtube.downloader import YouTubeDownloader
from clipforge.workers.base import BaseWorker

logger = logging.getLogger(__name__)

# Mapa plataforma -> classe do downloader.
_DOWNLOADER_CLASSES = {
    Platform.YOUTUBE: YouTubeDownloader,
    Platform.TIKTOK: TikTokDownloader,
    Platform.INSTAGRAM: InstagramDownloader,
}


class DownloaderWorker(BaseWorker):
    def __init__(
        self,
        db: Database,
        queue: JobQueue,
        worker_id: Optional[str] = None,
        poll_interval: float = 2.0,
        downloads_base_dir: Path | str = DOWNLOADS_DIR / "youtube",
    ):
        super().__init__(db, queue, worker_id=worker_id, poll_interval=poll_interval)
        # Mantido por compatibilidade — usado como default apenas quando a
        # plataforma no payload é YouTube (ou omitida, para não quebrar jobs
        # antigos enfileirados antes da generalização multi-plataforma).
        self.downloads_base_dir = Path(downloads_base_dir)

    def supported_job_types(self) -> List[JobType]:
        return [JobType.DOWNLOAD]

    def _build_downloader(self, platform: Platform, cookies_file: Optional[Path]):
        downloader_cls = _DOWNLOADER_CLASSES.get(platform)
        if downloader_cls is None:
            raise ValueError(f"Plataforma sem downloader implementado: {platform}")

        if platform == Platform.YOUTUBE:
            base_dir = self.downloads_base_dir
        else:
            base_dir = DOWNLOADS_DIR / platform.value

        archive_path = DOWNLOAD_ARCHIVE_FILES.get(platform.value)

        return downloader_cls(
            downloads_base_dir=base_dir,
            cookies_path=cookies_file,
            download_archive_path=archive_path,
        )

    def handle_job(self, job: Job) -> None:
        payload = job.payload
        video_id = payload.get("video_id")
        source_url = payload.get("source_url")
        channel_id = payload.get("channel_id")
        account_id = payload.get("account_id")
        use_cookies = payload.get("use_cookies", True)
        # Jobs antigos (pré-generalização multi-plataforma) não têm "platform"
        # no payload — assume YouTube, que era a única plataforma antes.
        platform = Platform(payload.get("platform", Platform.YOUTUBE.value))

        if not source_url:
            raise ValueError(f"Missing 'source_url' in job payload: {payload}")

        logger.info(f"Processing {platform.value} download for {source_url} (video_id={video_id})")

        # Determine cookies path if applicable (hoje só implementado de fato
        # para YouTube, via account.session_cookies ou o arquivo padrão).
        cookies_file: Optional[Path] = None
        if use_cookies and platform == Platform.YOUTUBE:
            if DEFAULT_YOUTUBE_COOKIES_FILE.exists():
                cookies_file = DEFAULT_YOUTUBE_COOKIES_FILE
            elif account_id:
                account = self.db.get_account(account_id)
                if account and account.session_cookies:
                    temp_cookies_file = DEFAULT_YOUTUBE_COOKIES_FILE
                    temp_cookies_file.parent.mkdir(parents=True, exist_ok=True)
                    temp_cookies_file.write_text(account.session_cookies, encoding="utf-8")
                    cookies_file = temp_cookies_file

        downloader = self._build_downloader(platform, cookies_file)

        try:
            file_path, metadata = downloader.download(
                url=source_url,
                channel_subfolder=channel_id,
            )

            # Update video record in DB
            now = datetime.now(timezone.utc)
            v_id = video_id or metadata.get("id") or f"vid_{now.timestamp()}"

            existing_video = self.db.get_video(v_id) or self.db.get_video_by_source_url(source_url)
            if existing_video:
                existing_video.local_path = str(file_path)
                existing_video.title = metadata.get("title") or existing_video.title
                existing_video.duration_seconds = metadata.get("duration_seconds")
                existing_video.width = metadata.get("width")
                existing_video.height = metadata.get("height")
                existing_video.kind = metadata.get("kind", VideoKind.UNKNOWN)
                existing_video.status = VideoStatus.DOWNLOADED
                existing_video.error_message = None
                existing_video.downloaded_at = now
                self.db.save_video(existing_video)
            else:
                new_video = Video(
                    id=v_id,
                    channel_id=channel_id,
                    source_url=source_url,
                    title=metadata.get("title"),
                    local_path=str(file_path),
                    kind=metadata.get("kind", VideoKind.UNKNOWN),
                    duration_seconds=metadata.get("duration_seconds"),
                    width=metadata.get("width"),
                    height=metadata.get("height"),
                    status=VideoStatus.DOWNLOADED,
                    downloaded_at=now,
                )
                self.db.save_video(new_video)

            logger.info(f"Successfully downloaded {source_url} -> {file_path}")

        except Exception as e:
            # Update DB with error status
            if video_id:
                video = self.db.get_video(video_id)
                if video:
                    video.status = VideoStatus.ERROR
                    video.error_message = str(e)
                    self.db.save_video(video)
            raise
