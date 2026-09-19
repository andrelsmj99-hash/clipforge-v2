"""
Playwright persistent session management for Canva authentication.
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import Optional

from playwright.sync_api import BrowserContext, Page, sync_playwright

from clipforge.core.config import SESSIONS_DIR

logger = logging.getLogger(__name__)

CANVA_SESSION_DIR = SESSIONS_DIR / "canva"


class CanvaSessionManager:
    def __init__(self, session_dir: Path | str = CANVA_SESSION_DIR):
        self.session_dir = Path(session_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def launch_context(self, playwright_instance, headless: bool = False) -> BrowserContext:
        """Launch a Chromium persistent context saving state to session_dir."""
        logger.info(f"Launching Canva browser session with data dir: {self.session_dir}")
        context = playwright_instance.chromium.launch_persistent_context(
            user_data_dir=str(self.session_dir),
            headless=headless,
            viewport={"width": 1440, "height": 900},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        return context

    def interactive_login(self, timeout_seconds: int = 300) -> bool:
        """
        Open a visible browser window allowing the user to log in to Canva.
        Keeps running until user logs in or timeout is reached.
        """
        logger.info("Opening visible browser for Canva login...")
        with sync_playwright() as p:
            context = self.launch_context(p, headless=False)
            page = context.new_page() if not context.pages else context.pages[0]

            page.goto("https://www.canva.com/login", wait_until="domcontentloaded")
            logger.info("Please log in to your Canva account in the opened browser window.")

            # Wait for user to reach logged in state (e.g. redirected away from /login)
            try:
                page.wait_for_url(lambda url: "/login" not in url and "canva.com" in url, timeout=timeout_seconds * 1000)
                logger.info("Login detected! Session cookies and local storage saved.")
                context.close()
                return True
            except Exception as e:
                logger.warning(f"Login timed out or was closed: {e}")
                context.close()
                return False
