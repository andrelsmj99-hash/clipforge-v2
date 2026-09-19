"""
TikTok Playwright persistent session management for TikTok Studio.
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import Optional

from playwright.sync_api import BrowserContext, Page, sync_playwright

from clipforge.core.config import SESSIONS_DIR

logger = logging.getLogger(__name__)

TIKTOK_SESSION_DIR = SESSIONS_DIR / "tiktok"


class TikTokSessionManager:
    def __init__(self, session_dir: Path | str = TIKTOK_SESSION_DIR):
        self.session_dir = Path(session_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def launch_context(self, playwright_instance, headless: bool = True) -> BrowserContext:
        """Launch a persistent Chromium context for TikTok automation."""
        logger.info(f"Launching TikTok browser session (headless={headless}) with dir: {self.session_dir}")
        context = playwright_instance.chromium.launch_persistent_context(
            user_data_dir=str(self.session_dir),
            headless=headless,
            viewport={"width": 1440, "height": 900},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        return context

    def interactive_login(self, timeout_seconds: int = 300) -> bool:
        """
        Open a visible browser window allowing the user to log in to TikTok / TikTok Studio.
        """
        logger.info("Opening visible browser for TikTok login...")
        with sync_playwright() as p:
            context = self.launch_context(p, headless=False)
            page = context.new_page() if not context.pages else context.pages[0]

            page.goto("https://www.tiktok.com/login", wait_until="domcontentloaded")
            logger.info("Please log in to your TikTok account in the browser window.")

            try:
                # Wait for user to navigate away from login or reach tiktok studio
                page.wait_for_url(
                    lambda url: "/login" not in url and "tiktok.com" in url,
                    timeout=timeout_seconds * 1000,
                )
                logger.info("TikTok login detected! Session state saved.")
                context.close()
                return True
            except Exception as e:
                logger.warning(f"TikTok login timed out or closed: {e}")
                context.close()
                return False

    def is_connected(self) -> bool:
        """Quick check if session directory has cookies or storage data."""
        cookie_file = self.session_dir / "Default" / "Network" / "Cookies"
        storage_dir = self.session_dir / "Default" / "Local Storage"
        return cookie_file.exists() or storage_dir.exists()
