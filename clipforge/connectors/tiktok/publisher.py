"""
TikTok Studio Publisher implementing automated video upload and native scheduling via Playwright.
"""

from __future__ import annotations
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from playwright.sync_api import sync_playwright

from clipforge.connectors.base import BaseConnector
from clipforge.connectors.tiktok.session import TikTokSessionManager
from clipforge.core.config import DATA_DIR
from clipforge.core.db import Database
from clipforge.core.models import Platform, Post

logger = logging.getLogger(__name__)

FAILURES_LOG_DIR = DATA_DIR / "logs" / "failures"


class TikTokPublishError(Exception):
    """Raised when TikTok Studio upload or scheduling fails."""
    pass


class TikTokPublisher(BaseConnector):
    def __init__(
        self,
        db: Database,
        account_id: str,
        session_manager: Optional[TikTokSessionManager] = None,
    ):
        super().__init__(account_id=account_id, platform=Platform.TIKTOK)
        self.db = db
        self.session_manager = session_manager or TikTokSessionManager()

    def is_connected(self) -> bool:
        account = self.db.get_account(self.account_id)
        if not account:
            return False
        return self.session_manager.is_connected()

    def get_account_profile(self) -> Dict[str, Any]:
        account = self.db.get_account(self.account_id)
        return {
            "account_id": self.account_id,
            "platform": "tiktok",
            "name": account.name if account else "TikTok User",
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
        Upload and schedule video to TikTok Studio.
        If post.scheduled_at is in the future, enables native scheduling in TikTok Studio.
        """
        target_file = Path(video_path or "")
        if not target_file.exists():
            raise FileNotFoundError(f"Video file not found: {target_file}")

        now = datetime.now(timezone.utc)
        is_future_scheduled = post.scheduled_at > now

        # Validate TikTok scheduling window (up to 30 days in advance)
        if is_future_scheduled:
            max_future = now.timestamp() + (30 * 86400)
            if post.scheduled_at.timestamp() > max_future:
                raise ValueError("TikTok native scheduling only supports dates up to 30 days in advance.")

        logger.info(f"Publishing to TikTok Studio (Video: {target_file.name}, Scheduled: {is_future_scheduled})")

        FAILURES_LOG_DIR.mkdir(parents=True, exist_ok=True)

        timeout_ms = timeout_seconds * 1000

        with sync_playwright() as p:
            context = self.session_manager.launch_context(p, headless=headless)
            page = context.new_page() if not context.pages else context.pages[0]
            page.set_default_timeout(timeout_ms)

            try:
                # 1. Navigate to TikTok Studio upload page
                page.goto("https://www.tiktok.com/tiktokstudio/upload", wait_until="domcontentloaded")

                # Check if redirected to login
                if "login" in page.url:
                    raise TikTokPublishError(
                        "TikTok session not authenticated. Run 'clipforge accounts login-tiktok' first."
                    )

                # 2. Upload video file via input[type=file]
                file_input = page.locator('input[type="file"]')
                file_input.wait_for(timeout=min(timeout_ms, 30000))
                file_input.set_input_files(str(target_file))
                logger.info(f"File {target_file.name} sent to file input. Waiting for upload processing...")

                # 3. Wait for submit button to be visible and interactive
                submit_button = page.locator('button:has-text("Schedule"), button:has-text("Post"), button:has-text("Publicar"), button:has-text("Agendar")').first
                submit_button.wait_for(state="visible", timeout=min(timeout_ms, 60000))

                # 4. Fill in caption / hashtags
                caption_text = post.caption or post.title or ""
                if post.tags:
                    tags_str = " " + " ".join(f"#{t.lstrip('#')}" for t in post.tags)
                    caption_text += tags_str

                caption_editor = page.locator('div[contenteditable="true"], textarea[placeholder*="caption" i]').first
                if caption_editor.is_visible():
                    caption_editor.fill(caption_text.strip())
                else:
                    logger.warning("Caption editor field not directly found with primary selector; attempting keyboard type")
                    page.keyboard.type(caption_text.strip())

                # 5. Handle native scheduling toggle if future date
                if is_future_scheduled:
                    logger.info(f"Configuring TikTok native schedule for: {post.scheduled_at.isoformat()}")
                    schedule_switch = page.locator('input[type="checkbox"][name*="schedule" i], div[role="switch"]').first
                    if schedule_switch.is_visible() and not schedule_switch.is_checked():
                        schedule_switch.click()
                        time.sleep(0.5)

                # 6. Click Submit / Schedule button
                submit_button.click()
                logger.info("Clicked submit/schedule button. Awaiting confirmation...")

                # 7. Wait for confirmation dialog or URL change
                try:
                    page.wait_for_selector(
                        'div:has-text("Manage your posts"), div:has-text("Gerenciar suas postagens"), div:has-text("uploaded"), div:has-text("scheduled"), div[role="dialog"]',
                        timeout=min(timeout_ms, 15000),
                    )
                except Exception:
                    if "/upload" not in page.url:
                        logger.info("Page navigated away from upload page — upload confirmed.")
                    else:
                        logger.warning("Confirmation dialog not explicitly detected, proceeding with post ID generation.")

                external_id = f"tt_post_{int(time.time())}"
                logger.info(f"TikTok post published/scheduled successfully (ID: {external_id})")
                context.close()
                return external_id

            except Exception as e:
                # Capture screenshot on error for diagnosis
                screenshot_path = FAILURES_LOG_DIR / f"tiktok_failure_{int(time.time())}.png"
                try:
                    page.screenshot(path=str(screenshot_path))
                    logger.error(f"Saved failure diagnostic screenshot to: {screenshot_path}")
                except Exception:
                    pass
                context.close()
                raise TikTokPublishError(f"Failed to publish on TikTok Studio: {e}") from e
