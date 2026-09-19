"""Tests for DownloaderWorker and PublisherWorker execution loop."""

from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from clipforge.core.db import Database
from clipforge.core.models import (
    Account,
    AccountStatus,
    JobStatus,
    JobType,
    Platform,
    Post,
    PostStatus,
    Video,
    VideoKind,
    VideoStatus,
)
from clipforge.core.queue import JobQueue
from clipforge.workers.downloader import DownloaderWorker
from clipforge.workers.publisher import PublisherWorker


@pytest.fixture
def test_env(tmp_path):
    db = Database(db_path=tmp_path / "test_workers.db")
    queue = JobQueue(db)
    return db, queue, tmp_path


def test_downloader_worker_success(test_env):
    db, queue, tmp_path = test_env
    worker = DownloaderWorker(db, queue, poll_interval=0.1, downloads_base_dir=tmp_path / "downloads")

    # Create test video in DB
    test_video = Video(
        id="vid_test_1",
        source_url="https://www.youtube.com/watch?v=mock123",
        status=VideoStatus.PENDING,
    )
    db.save_video(test_video)

    # Enqueue job
    job = queue.enqueue(
        job_type=JobType.DOWNLOAD,
        payload={"video_id": "vid_test_1", "source_url": "https://www.youtube.com/watch?v=mock123"},
    )

    fake_file = tmp_path / "downloads" / "mock123.mp4"
    fake_file.parent.mkdir(parents=True, exist_ok=True)
    fake_file.write_text("fake video content")

    mock_metadata = {
        "id": "mock123",
        "title": "Mock Video Title",
        "duration_seconds": 45.0,
        "width": 1080,
        "height": 1920,
        "kind": VideoKind.SHORT,
    }

    with patch("clipforge.workers.downloader.YouTubeDownloader.download", return_value=(fake_file, mock_metadata)):
        processed = worker.run_once()
        assert processed is True

    # Check job status
    finished_job = queue.get_job(job.id)
    assert finished_job.status == JobStatus.COMPLETED

    # Check video in DB
    updated_video = db.get_video("vid_test_1")
    assert updated_video.status == VideoStatus.DOWNLOADED
    assert updated_video.title == "Mock Video Title"
    assert updated_video.kind == VideoKind.SHORT
    assert updated_video.duration_seconds == 45.0
    assert updated_video.local_path == str(fake_file)


def test_publisher_worker_future_schedule(test_env):
    db, queue, tmp_path = test_env
    worker = PublisherWorker(db, queue, poll_interval=0.1)

    # Setup account
    acc = Account(
        id="yt_acc_1",
        platform=Platform.YOUTUBE,
        name="Channel 1",
        status=AccountStatus.CONNECTED,
        credentials_json='{"token": "xyz"}',
    )
    db.save_account(acc)

    # Setup future post
    fake_video = tmp_path / "rendered.mp4"
    fake_video.write_text("video binary")

    sched_time = datetime.now(timezone.utc) + timedelta(days=1)
    post = Post(
        id="post_future",
        account_id="yt_acc_1",
        title="Scheduled Short",
        caption="#shorts test",
        scheduled_at=sched_time,
        status=PostStatus.DRAFT,
    )
    db.save_post(post)

    job = queue.enqueue(
        job_type=JobType.PUBLISH,
        payload={
            "post_id": "post_future",
            "account_id": "yt_acc_1",
            "platform": "youtube",
            "video_path": str(fake_video),
        },
    )

    with patch("clipforge.connectors.youtube.publisher.YouTubePublisher.publish_video", return_value="yt_video_id_999"):
        processed = worker.run_once()
        assert processed is True

    # Check job & post status
    finished_job = queue.get_job(job.id)
    assert finished_job.status == JobStatus.COMPLETED

    updated_post = db.get_post("post_future")
    assert updated_post.status == PostStatus.SCHEDULED
    assert updated_post.external_post_id == "yt_video_id_999"


def test_publisher_worker_tiktok_schedule(test_env):
    db, queue, tmp_path = test_env
    worker = PublisherWorker(db, queue, poll_interval=0.1)

    acc = Account(
        id="tt_acc_work",
        platform=Platform.TIKTOK,
        name="@ttcreator",
        status=AccountStatus.CONNECTED,
    )
    db.save_account(acc)

    fake_video = tmp_path / "rendered_tt.mp4"
    fake_video.write_text("video binary")

    sched_time = datetime.now(timezone.utc) + timedelta(days=2)
    post = Post(
        id="post_tt_work",
        account_id="tt_acc_work",
        platform=Platform.TIKTOK,
        title="TikTok Scheduled",
        caption="#viral",
        scheduled_at=sched_time,
        status=PostStatus.DRAFT,
    )
    db.save_post(post)

    job = queue.enqueue(
        job_type=JobType.PUBLISH,
        payload={
            "post_id": "post_tt_work",
            "account_id": "tt_acc_work",
            "platform": "tiktok",
            "video_path": str(fake_video),
        },
    )

    with patch("clipforge.connectors.tiktok.publisher.TikTokPublisher.publish_video", return_value="tt_vid_123"):
        processed = worker.run_once()
        assert processed is True

    finished_job = queue.get_job(job.id)
    assert finished_job.status == JobStatus.COMPLETED

    updated_post = db.get_post("post_tt_work")
    assert updated_post.status == PostStatus.SCHEDULED
    assert updated_post.external_post_id == "tt_vid_123"


def test_publisher_worker_instagram_schedule(test_env):
    db, queue, tmp_path = test_env
    worker = PublisherWorker(db, queue, poll_interval=0.1)

    acc = Account(
        id="ig_acc_work",
        platform=Platform.INSTAGRAM,
        name="@igcreator",
        status=AccountStatus.CONNECTED,
    )
    db.save_account(acc)

    fake_video = tmp_path / "rendered_ig.mp4"
    fake_video.write_text("video binary")

    sched_time = datetime.now(timezone.utc) + timedelta(days=3)
    post = Post(
        id="post_ig_work",
        account_id="ig_acc_work",
        platform=Platform.INSTAGRAM,
        title="Instagram Scheduled",
        caption="#reels",
        scheduled_at=sched_time,
        status=PostStatus.DRAFT,
    )
    db.save_post(post)

    job = queue.enqueue(
        job_type=JobType.PUBLISH,
        payload={
            "post_id": "post_ig_work",
            "account_id": "ig_acc_work",
            "platform": "instagram",
            "video_path": str(fake_video),
        },
    )

    with patch("clipforge.connectors.instagram.publisher.InstagramPublisher.publish_video", return_value="ig_vid_456"):
        processed = worker.run_once()
        assert processed is True

    finished_job = queue.get_job(job.id)
    assert finished_job.status == JobStatus.COMPLETED

    updated_post = db.get_post("post_ig_work")
    assert updated_post.status == PostStatus.SCHEDULED
    assert updated_post.external_post_id == "ig_vid_456"

