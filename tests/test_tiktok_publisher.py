"""
Tests for TikTok Studio Publisher & Playwright automation.
"""

from __future__ import annotations
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from clipforge.connectors.tiktok.publisher import TikTokPublisher, TikTokPublishError
from clipforge.connectors.tiktok.session import TikTokSessionManager
from clipforge.core.db import Database
from clipforge.core.models import Account, AccountStatus, Platform, Post, PostStatus


@pytest.fixture
def env(tmp_path):
    db = Database(db_path=tmp_path / "test_tiktok.db")
    acc = Account(
        id="tt_acc_1",
        platform=Platform.TIKTOK,
        name="@testcreator",
        status=AccountStatus.CONNECTED,
    )
    db.save_account(acc)

    video_file = tmp_path / "tiktok_clip.mp4"
    video_file.write_bytes(b"tiktok video content")

    post = Post(
        id="post_tt_1",
        account_id=acc.id,
        platform=Platform.TIKTOK,
        title="Epic TikTok",
        caption="Check this out! #viral #fyp",
        tags=["viral", "fyp"],
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=2),
        status=PostStatus.DRAFT,
    )
    db.save_post(post)

    return db, acc, post, video_file


def test_tiktok_publisher_rejects_missing_file(env):
    db, acc, post, _ = env
    publisher = TikTokPublisher(db=db, account_id=acc.id)

    with pytest.raises(FileNotFoundError):
        publisher.publish_video(post=post, video_path="/nonexistent/path.mp4")


def test_tiktok_publisher_rejects_over_30_days_schedule(env):
    db, acc, post, video_file = env
    publisher = TikTokPublisher(db=db, account_id=acc.id)

    # Set schedule beyond 30 days (e.g. 40 days)
    post.scheduled_at = datetime.now(timezone.utc) + timedelta(days=40)

    with pytest.raises(ValueError, match="up to 30 days"):
        publisher.publish_video(post=post, video_path=str(video_file))


def test_tiktok_publisher_is_connected(env):
    db, acc, _, _ = env
    mock_session = MagicMock(spec=TikTokSessionManager)
    mock_session.is_connected.return_value = True

    publisher = TikTokPublisher(db=db, account_id=acc.id, session_manager=mock_session)
    assert publisher.is_connected() is True

    mock_session.is_connected.return_value = False
    assert publisher.is_connected() is False


def test_tiktok_publisher_successful_playwright_flow(env):
    db, acc, post, video_file = env

    mock_page = MagicMock()
    mock_page.url = "https://www.tiktok.com/tiktokstudio/upload"
    mock_locator = MagicMock()
    mock_locator.is_visible.return_value = True
    mock_locator.is_checked.return_value = False
    mock_page.locator.return_value = mock_locator

    mock_context = MagicMock()
    mock_context.pages = [mock_page]

    mock_session = MagicMock(spec=TikTokSessionManager)
    mock_session.launch_context.return_value = mock_context

    publisher = TikTokPublisher(db=db, account_id=acc.id, session_manager=mock_session)

    with patch("clipforge.connectors.tiktok.publisher.sync_playwright") as mock_pw, \
         patch("clipforge.connectors.tiktok.publisher.time.sleep"):
        pw_instance = MagicMock()
        mock_pw.return_value.__enter__.return_value = pw_instance

        post_id = publisher.publish_video(post=post, video_path=str(video_file))
        assert post_id.startswith("tt_post_")

        mock_page.goto.assert_called_once_with(
            "https://www.tiktok.com/tiktokstudio/upload", wait_until="domcontentloaded"
        )
        mock_context.close.assert_called_once()
