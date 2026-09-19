"""
Tests for Instagram Reels / Meta Business Suite Publisher & Playwright automation.
"""

from __future__ import annotations
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from clipforge.connectors.instagram.publisher import InstagramPublisher, InstagramPublishError
from clipforge.connectors.instagram.session import InstagramSessionManager
from clipforge.core.db import Database
from clipforge.core.models import Account, AccountStatus, Platform, Post, PostStatus


@pytest.fixture
def env(tmp_path):
    db = Database(db_path=tmp_path / "test_instagram.db")
    acc = Account(
        id="ig_acc_1",
        platform=Platform.INSTAGRAM,
        name="@testreels",
        status=AccountStatus.CONNECTED,
    )
    db.save_account(acc)

    video_file = tmp_path / "insta_reel.mp4"
    video_file.write_bytes(b"instagram reel content")

    post = Post(
        id="post_ig_1",
        account_id=acc.id,
        platform=Platform.INSTAGRAM,
        title="Epic Reel",
        caption="Check out this reel! #reels #viral",
        tags=["reels", "viral"],
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=5),
        status=PostStatus.DRAFT,
    )
    db.save_post(post)

    return db, acc, post, video_file


def test_instagram_publisher_rejects_missing_file(env):
    db, acc, post, _ = env
    publisher = InstagramPublisher(db=db, account_id=acc.id)

    with pytest.raises(FileNotFoundError):
        publisher.publish_video(post=post, video_path="/nonexistent/path.mp4")


def test_instagram_publisher_rejects_over_75_days_schedule(env):
    db, acc, post, video_file = env
    publisher = InstagramPublisher(db=db, account_id=acc.id)

    # Set schedule beyond 75 days (e.g. 80 days)
    post.scheduled_at = datetime.now(timezone.utc) + timedelta(days=80)

    with pytest.raises(ValueError, match="up to 75 days"):
        publisher.publish_video(post=post, video_path=str(video_file))


def test_instagram_publisher_is_connected(env):
    db, acc, _, _ = env
    mock_session = MagicMock(spec=InstagramSessionManager)
    mock_session.is_connected.return_value = True

    publisher = InstagramPublisher(db=db, account_id=acc.id, session_manager=mock_session)
    assert publisher.is_connected() is True

    mock_session.is_connected.return_value = False
    assert publisher.is_connected() is False


def test_instagram_publisher_successful_playwright_flow(env):
    db, acc, post, video_file = env

    mock_page = MagicMock()
    mock_page.url = "https://business.facebook.com/latest/composer"
    mock_locator = MagicMock()
    mock_locator.is_visible.return_value = True
    mock_page.locator.return_value = mock_locator

    mock_context = MagicMock()
    mock_context.pages = [mock_page]

    mock_session = MagicMock(spec=InstagramSessionManager)
    mock_session.launch_context.return_value = mock_context

    publisher = InstagramPublisher(db=db, account_id=acc.id, session_manager=mock_session)

    with patch("clipforge.connectors.instagram.publisher.sync_playwright") as mock_pw, \
         patch("clipforge.connectors.instagram.publisher.time.sleep"):
        pw_instance = MagicMock()
        mock_pw.return_value.__enter__.return_value = pw_instance

        post_id = publisher.publish_video(post=post, video_path=str(video_file))
        assert post_id.startswith("ig_post_")

        mock_page.goto.assert_called_once_with(
            "https://business.facebook.com/latest/composer", wait_until="domcontentloaded"
        )
        mock_locator.first.set_input_files.assert_called_once_with(str(video_file))
        mock_context.close.assert_called_once()


def test_instagram_publisher_redirect_to_login_error(env):
    db, acc, post, video_file = env

    mock_page = MagicMock()
    mock_page.url = "https://business.facebook.com/login"

    mock_context = MagicMock()
    mock_context.pages = [mock_page]

    mock_session = MagicMock(spec=InstagramSessionManager)
    mock_session.launch_context.return_value = mock_context

    publisher = InstagramPublisher(db=db, account_id=acc.id, session_manager=mock_session)

    with patch("clipforge.connectors.instagram.publisher.sync_playwright") as mock_pw:
        pw_instance = MagicMock()
        mock_pw.return_value.__enter__.return_value = pw_instance

        with pytest.raises(InstagramPublishError, match="not authenticated"):
            publisher.publish_video(post=post, video_path=str(video_file))
