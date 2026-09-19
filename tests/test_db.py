"""Tests for SQLite database manager and models."""

import pytest
from datetime import datetime, timezone, timedelta
from clipforge.core.db import Database
from clipforge.core.models import (
    Account,
    AccountStatus,
    Channel,
    Platform,
    Post,
    PostStatus,
    Video,
    VideoKind,
    VideoStatus,
)


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test_clipforge.db"
    return Database(db_path=db_file)


def test_account_crud(test_db):
    acc = Account(
        id="yt_test_123",
        platform=Platform.YOUTUBE,
        name="Test YouTube Channel",
        status=AccountStatus.CONNECTED,
        credentials_json='{"token": "xyz"}',
        token_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    test_db.save_account(acc)

    fetched = test_db.get_account("yt_test_123")
    assert fetched is not None
    assert fetched.name == "Test YouTube Channel"
    assert fetched.platform == Platform.YOUTUBE
    assert fetched.status == AccountStatus.CONNECTED
    assert fetched.token_expires_at is not None

    # Update
    acc.status = AccountStatus.EXPIRED
    test_db.save_account(acc)
    updated = test_db.get_account("yt_test_123")
    assert updated.status == AccountStatus.EXPIRED


def test_video_deduplication(test_db):
    v1 = Video(
        id="v1",
        source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        title="Rick Roll",
        kind=VideoKind.LONG,
        duration_seconds=212.0,
        status=VideoStatus.DOWNLOADED,
    )
    test_db.save_video(v1)

    # Lookup by source_url for deduplication
    found = test_db.get_video_by_source_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert found is not None
    assert found.id == "v1"
    assert found.title == "Rick Roll"

    notFound = test_db.get_video_by_source_url("https://www.youtube.com/watch?v=nonexistent")
    assert notFound is None


def test_post_scheduling(test_db):
    acc = Account(id="acc1", platform=Platform.YOUTUBE, name="Chan1", status=AccountStatus.CONNECTED)
    test_db.save_account(acc)

    sched_time = datetime.now(timezone.utc) + timedelta(hours=2)
    post = Post(
        id="post1",
        account_id="acc1",
        title="My Scheduled Short",
        caption="Check this out! #shorts",
        tags=["shorts", "viral"],
        scheduled_at=sched_time,
        status=PostStatus.DRAFT,
    )
    test_db.save_post(post)

    fetched_post = test_db.get_post("post1")
    assert fetched_post is not None
    assert fetched_post.title == "My Scheduled Short"
    assert "viral" in fetched_post.tags
    assert fetched_post.status == PostStatus.DRAFT
