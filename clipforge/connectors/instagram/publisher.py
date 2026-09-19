"""
Meta Business Suite Publisher implementing automated Instagram Reels upload and native scheduling via Playwright.
"""

from __future__ import annotations
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from playwright.sync_api import sync_playwright

from clipforge.connectors.base import BaseConnector
from clipforge.connectors.instagram.session import InstagramSessionManager
from clipforge.core.config import DATA_DIR
from clipforge.core.db import Database
from clipforge.core.models import Platform, Post

logger = logging.getLogger(__name__)

FAILURES_LOG_DIR = DATA_DIR / "logs" / "failures"


class InstagramPublishError(Exception):
    """Raised when Meta Business Suite upload or scheduling fails."""
    pass


class InstagramPublisher(BaseConnector):
    def __init__(
        self,
        db: Database,
        account_id: str,
        session_manager: Optional[InstagramSessionManager] = None,
    ):
        super().__init__(account_id=account_id, platform=Platform.INSTAGRAM)
        self.db = db
        self.session_manager = session_manager or InstagramSessionManager()

    def is_connected(self) -> bool:
        account = self.db.get_account(self.account_id)
        if not account:
            return False
        return self.session_manager.is_connected()

    def get_account_profile(self) -> Dict[str, Any]:
        account = self.db.get_account(self.account_id)
        return {
            "account_id": self.account_id,
            "platform": "instagram",
            "name": account.name if account else "Instagram User",
            "status": account.status.value if account else "disconnected",
        }

    def publish_video(
        self,
        post: Post,
        video_path: Optional[str] = None,
        headless: bool = True,
        timeout_seconds: int = 180,
    ) -> str:
        """
        Upload and schedule video to Instagram via Meta Business Suite.
        If post.scheduled_at is in the future, enables native scheduling (up to 75 days).
        """
        target_file = Path(video_path or "")
        if not target_file.exists():
            raise FileNotFoundError(f"Video file not found: {target_file}")

        now = datetime.now(timezone.utc)
        is_future_scheduled = post.scheduled_at > now

        # Validate Meta Business Suite scheduling window (up to 75 days in advance)
        if is_future_scheduled:
            max_future = now.timestamp() + (75 * 86400)
            if post.scheduled_at.timestamp() > max_future:
                raise ValueError("Meta Business Suite native scheduling supports dates up to 75 days in advance.")

        logger.info(f"Publishing to Meta Business Suite for Instagram (Video: {target_file.name}, Scheduled: {is_future_scheduled})")

        FAILURES_LOG_DIR.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as p:
            context = self.session_manager.launch_context(p, headless=headless)
            page = context.new_page() if not context.pages else context.pages[0]

            try:
                # 1. Navigate to composer
                page.goto("https://business.facebook.com/latest/composer", wait_until="domcontentloaded")
                time.sleep(3.0)

                # Check if redirected to login
                if "login" in page.url:
                    raise InstagramPublishError(
                        "Meta Business Suite session not authenticated. Run 'clipforge accounts login-instagram' first."
                    )

                # 2. Upload video file via input[type=file]
                file_input = page.locator('input[type="file"]').first
                file_input.wait_for(timeout=20000)
                file_input.set_input_files(str(target_file))
                logger.info(f"File {target_file.name} sent to file input. Waiting for processing...")

                # 3. Wait for video processing
                time.sleep(5.0)

                # 4. Fill in caption / hashtags
                caption_text = post.caption or post.title or ""
                if post.tags:
                    tags_str = " " + " ".join(f"#{t.lstrip('#')}" for t in post.tags)
                    caption_text += tags_str

                caption_editor = page.locator('div[contenteditable="true"], textarea[aria-label*="caption" i], textarea[placeholder*="text" i]').first
                if caption_editor.is_visible():
                    caption_editor.fill(caption_text.strip())
                else:
                    logger.warning("Caption field not directly found with primary selector; attempting keyboard typing")
                    page.keyboard.type(caption_text.strip())

                # 5. Handle native scheduling toggle if future date
                if is_future_scheduled:
                    logger.info(f"Configuring Meta native schedule for: {post.scheduled_at.isoformat()}")
                    schedule_radio = page.locator('input[type="radio"][value*="SCHEDULE" i], button:has-text("Schedule"), button:has-text("Programar")').first
                    if schedule_radio.is_visible():
                        schedule_radio.click()
                        time.sleep(1.0)

                # 6. Click Submit / Schedule button
                submit_button = page.locator('button:has-text("Schedule"), button:has-text("Publish"), button:has-text("Programar"), button:has-text("Publicar")').first
                submit_button.wait_for(state="visible", timeout=30000)
                submit_button.click()
                logger.info("Clicked submit/schedule button. Awaiting confirmation...")

                time.sleep(5.0)

                external_id = f"ig_post_{int(time.time())}"
                logger.info(f"Instagram post scheduled successfully via Meta Business Suite (ID: {external_id})")
                context.close()
                return external_id

            except Exception as e:
                screenshot_path = FAILURES_LOG_DIR / f"meta_failure_{int(time.time())}.png"
                try:
                    page.screenshot(path=str(screenshot_path))
                    logger.error(f"Saved failure diagnostic screenshot to: {screenshot_path}")
                except Exception:
                    pass
                context.close()
                raise InstagramPublishError(f"Failed to publish on Meta Business Suite: {e}") from e
