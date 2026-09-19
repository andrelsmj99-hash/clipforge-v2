"""
Tests for the yt-dlp consolidation decision:
- YouTube: multi-tab listing (videos + shorts + streams) and live_vod classification
- TikTok/Instagram: downloader dispatch through the shared yt-dlp base
- download_archive parameter is passed through to yt-dlp
"""

from __future__ import annotations
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from clipforge.core.db import Database
from clipforge.core.models import JobStatus, JobType, Platform, Video, VideoKind, VideoStatus
from clipforge.core.queue import JobQueue
from clipforge.modules.common.ytdlp_base import ExtractorFragileError, VideoUnavailableError
from clipforge.modules.instagram.downloader import InstagramDownloader
from clipforge.modules.tiktok.downloader import TikTokDownloader
from clipforge.modules.youtube.scraper import YouTubeScraper, classify_video_kind
from clipforge.workers.downloader import DownloaderWorker


# --- Classification -----------------------------------------------------

def test_classify_live_vod_by_source_tab():
    assert classify_video_kind(duration=7200.0, source_tab="streams") == VideoKind.LIVE_VOD
    # Even a short-duration entry from the streams tab is still a live VOD.
    assert classify_video_kind(duration=30.0, source_tab="streams") == VideoKind.LIVE_VOD


def test_classify_short_regardless_of_aspect_ratio():
    # Horizontal, <=60s: still SHORT (YouTube's 2024+ policy no longer
    # requires vertical aspect for Shorts) — this replaces the old dead
    # branch that always returned SHORT either way.
    assert classify_video_kind(duration=40.0, width=1920, height=1080) == VideoKind.SHORT
    assert classify_video_kind(duration=40.0, width=1080, height=1920) == VideoKind.SHORT


# --- Multi-tab channel listing -------------------------------------------

def _fake_ydl_for_tab(entries_by_tab):
    """Build a fake yt_dlp.YoutubeDL context manager keyed by URL substring."""
    def _factory(opts):
        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)

        def extract_info(url, download=False):
            for tab, entries in entries_by_tab.items():
                if f"/{tab}" in url:
                    return {"entries": entries}
            return {"entries": []}

        mock_ydl.extract_info.side_effect = extract_info
        return mock_ydl
    return _factory


def test_list_channel_all_tabs_merges_and_dedups():
    entries_by_tab = {
        "videos": [{"id": "v1", "title": "Long video", "duration": 300, "url": "https://youtube.com/watch?v=v1"}],
        "shorts": [{"id": "s1", "title": "A short", "duration": 30, "url": "https://youtube.com/shorts/s1"}],
        "streams": [{"id": "l1", "title": "Old livestream", "duration": 7200, "url": "https://youtube.com/watch?v=l1"}],
    }
    scraper = YouTubeScraper()

    with patch("yt_dlp.YoutubeDL", side_effect=_fake_ydl_for_tab(entries_by_tab)):
        results = scraper.list_channel_all_tabs("@somechannel", include_lives=True)

    ids = {r["id"] for r in results}
    assert ids == {"v1", "s1", "l1"}

    kinds = {r["id"]: r["kind"] for r in results}
    assert kinds["v1"] == VideoKind.LONG
    assert kinds["s1"] == VideoKind.SHORT
    assert kinds["l1"] == VideoKind.LIVE_VOD


def test_list_channel_all_tabs_excludes_lives_when_requested():
    entries_by_tab = {
        "videos": [{"id": "v1", "title": "Long video", "duration": 300, "url": "https://youtube.com/watch?v=v1"}],
        "shorts": [],
        "streams": [{"id": "l1", "title": "Old livestream", "duration": 7200, "url": "https://youtube.com/watch?v=l1"}],
    }
    scraper = YouTubeScraper()

    with patch("yt_dlp.YoutubeDL", side_effect=_fake_ydl_for_tab(entries_by_tab)):
        results = scraper.list_channel_all_tabs("@somechannel", include_lives=False)

    ids = {r["id"] for r in results}
    assert ids == {"v1"}


def test_list_channel_all_tabs_deduplicates_overlap_across_tabs():
    # Same video id showing up in two tabs should only appear once.
    entries_by_tab = {
        "videos": [{"id": "dup1", "title": "Dup", "duration": 300, "url": "https://youtube.com/watch?v=dup1"}],
        "shorts": [{"id": "dup1", "title": "Dup", "duration": 300, "url": "https://youtube.com/watch?v=dup1"}],
        "streams": [],
    }
    scraper = YouTubeScraper()

    with patch("yt_dlp.YoutubeDL", side_effect=_fake_ydl_for_tab(entries_by_tab)):
        results = scraper.list_channel_all_tabs("@somechannel", include_lives=True)

    assert len(results) == 1


# --- Downloader dispatch by platform (worker) ----------------------------

@pytest.fixture
def test_env(tmp_path):
    db = Database(db_path=tmp_path / "test_multiplatform.db")
    jq = JobQueue(db)
    return db, jq, tmp_path


def test_downloader_worker_dispatches_tiktok(test_env):
    db, jq, tmp_path = test_env
    worker = DownloaderWorker(db, jq, poll_interval=0.1, downloads_base_dir=tmp_path / "downloads" / "youtube")

    test_video = Video(id="vid_tt_1", source_url="https://www.tiktok.com/@user/video/123", status=VideoStatus.PENDING)
    db.save_video(test_video)

    job = jq.enqueue(
        job_type=JobType.DOWNLOAD,
        payload={
            "video_id": "vid_tt_1",
            "source_url": "https://www.tiktok.com/@user/video/123",
            "platform": Platform.TIKTOK.value,
        },
    )

    fake_file = tmp_path / "downloads" / "tiktok" / "123.mp4"
    fake_file.parent.mkdir(parents=True, exist_ok=True)
    fake_file.write_text("fake tiktok video")
    mock_metadata = {"id": "123", "title": "TikTok clip", "duration_seconds": 20.0, "kind": VideoKind.SHORT}

    with patch("clipforge.workers.downloader.TikTokDownloader.download", return_value=(fake_file, mock_metadata)):
        processed = worker.run_once()
        assert processed is True

    finished_job = jq.get_job(job.id)
    assert finished_job.status == JobStatus.COMPLETED

    updated_video = db.get_video("vid_tt_1")
    assert updated_video.status == VideoStatus.DOWNLOADED
    assert updated_video.title == "TikTok clip"


def test_downloader_worker_dispatches_instagram(test_env):
    db, jq, tmp_path = test_env
    worker = DownloaderWorker(db, jq, poll_interval=0.1, downloads_base_dir=tmp_path / "downloads" / "youtube")

    test_video = Video(id="vid_ig_1", source_url="https://www.instagram.com/p/abc123/", status=VideoStatus.PENDING)
    db.save_video(test_video)

    job = jq.enqueue(
        job_type=JobType.DOWNLOAD,
        payload={
            "video_id": "vid_ig_1",
            "source_url": "https://www.instagram.com/p/abc123/",
            "platform": Platform.INSTAGRAM.value,
        },
    )

    fake_file = tmp_path / "downloads" / "instagram" / "abc123.mp4"
    fake_file.parent.mkdir(parents=True, exist_ok=True)
    fake_file.write_text("fake instagram video")
    mock_metadata = {"id": "abc123", "title": "IG reel", "duration_seconds": 15.0, "kind": VideoKind.SHORT}

    with patch("clipforge.workers.downloader.InstagramDownloader.download", return_value=(fake_file, mock_metadata)):
        processed = worker.run_once()
        assert processed is True

    updated_video = db.get_video("vid_ig_1")
    assert updated_video.status == VideoStatus.DOWNLOADED


# --- Instagram fragile-extractor error wrapping ---------------------------

def test_instagram_downloader_wraps_unknown_error_as_fragile(tmp_path):
    downloader = InstagramDownloader(downloads_base_dir=tmp_path / "ig")

    class FakeYtDlpError(Exception):
        pass

    import yt_dlp
    with patch.object(yt_dlp, "YoutubeDL") as mock_ydl_cls:
        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.side_effect = yt_dlp.utils.DownloadError("some unexpected new instagram error")
        mock_ydl_cls.return_value = mock_ydl

        with pytest.raises(ExtractorFragileError):
            downloader.download("https://www.instagram.com/p/unknown/")


def test_instagram_downloader_classifies_private_as_unavailable(tmp_path):
    downloader = InstagramDownloader(downloads_base_dir=tmp_path / "ig")

    import yt_dlp
    with patch.object(yt_dlp, "YoutubeDL") as mock_ydl_cls:
        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.side_effect = yt_dlp.utils.DownloadError("This content is private")
        mock_ydl_cls.return_value = mock_ydl

        with pytest.raises(VideoUnavailableError):
            downloader.download("https://www.instagram.com/p/private123/")


# --- download_archive plumbing --------------------------------------------

def test_download_archive_path_passed_to_ydl_opts(tmp_path):
    archive_path = tmp_path / "archive.txt"
    downloader = TikTokDownloader(downloads_base_dir=tmp_path / "tt", download_archive_path=archive_path)

    captured_opts = {}

    import yt_dlp

    class FakeYDL:
        def __init__(self, opts):
            captured_opts.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            return {"id": "vid1", "title": "t", "duration": 10}

        def prepare_filename(self, info):
            return str(tmp_path / "tt" / "vid1.mp4")

    (tmp_path / "tt").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tt" / "vid1.mp4").write_text("x")

    with patch.object(yt_dlp, "YoutubeDL", FakeYDL):
        downloader.download("https://www.tiktok.com/@user/video/vid1")

    assert captured_opts.get("download_archive") == str(archive_path)
