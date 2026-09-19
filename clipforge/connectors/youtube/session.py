"""YouTube Playwright browser session capture for extracting cookies to bypass age restriction."""

from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from clipforge.core.config import DEFAULT_YOUTUBE_COOKIES_FILE, SESSIONS_DIR
from clipforge.core.db import Database

logger = logging.getLogger(__name__)


def cookies_to_netscape(cookies: List[Dict[str, Any]]) -> str:
    """
    Convert Playwright cookie objects into standard Netscape HTTP Cookie File format
    required by yt-dlp.
    Format: domain, flag, path, secure, expiration, name, value
    """
    lines = [
        "# Netscape HTTP Cookie File",
        "# Exported by Clip Forge V2 for yt-dlp authentication",
        "",
    ]

    for cookie in cookies:
        domain = cookie.get("domain", "")
        # Netscape flag: TRUE if domain starts with '.', FALSE otherwise
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        path = cookie.get("path", "/")
        secure = "TRUE" if cookie.get("secure", False) else "FALSE"
        
        # Expires is timestamp (int/float) or 0
        expires = cookie.get("expires", -1)
        if expires is None or expires == -1:
            # Session cookie or far future
            expires_str = "0"
        else:
            expires_str = str(int(expires))

        name = cookie.get("name", "")
        value = cookie.get("value", "")

        lines.append(f"{domain}\t{flag}\t{path}\t{secure}\t{expires_str}\t{name}\t{value}")

    return "\n".join(lines) + "\n"


class YouTubeSessionManager:
    def __init__(self, db: Database, cookies_file: Path = DEFAULT_YOUTUBE_COOKIES_FILE):
        self.db = db
        self.cookies_file = cookies_file
        self.cookies_file.parent.mkdir(parents=True, exist_ok=True)

    def extract_and_save_session(
        self,
        account_id: Optional[str] = None,
        user_data_dir: Optional[Path] = None,
        timeout_seconds: int = 180,
    ) -> str:
        """
        Launch an interactive Playwright Chromium browser so the user can log into YouTube.
        Once authenticated, extracts all cookies, formats as Netscape, saves to file and database.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise ImportError(
                "Playwright is not installed. Please run: pip install playwright && playwright install chromium"
            ) from e

        data_dir = user_data_dir or (SESSIONS_DIR / "browser_profile")
        data_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Launching browser session for YouTube login. Data directory: {data_dir}")

        with sync_playwright() as p:
            # Launch persistent context to keep login session between runs
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(data_dir),
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                viewport={"width": 1280, "height": 800},
            )

            page = context.new_page()
            page.goto("https://www.youtube.com")

            print("\n" + "=" * 60)
            print("POR FAVOR, FAÇA LOGIN NA CONTA DO YOUTUBE NO NAVEGADOR QUE ABRIU.")
            print("Após o login ser concluído e você estiver no YouTube, pressione ENTER no terminal...")
            print("=" * 60 + "\n")

            try:
                # Wait for user confirmation in terminal or page navigation
                input("Pressione [ENTER] quando estiver conectado no YouTube: ")
            except EOFError:
                # In non-interactive environments, wait for page state
                page.wait_for_timeout(5000)

            # Retrieve all cookies
            cookies = context.cookies(["https://www.youtube.com", "https://accounts.google.com"])
            context.close()

        if not cookies:
            raise RuntimeError("No cookies captured from YouTube session.")

        netscape_cookies = cookies_to_netscape(cookies)
        self.cookies_file.write_text(netscape_cookies, encoding="utf-8")
        logger.info(f"Saved Netscape cookies to {self.cookies_file}")

        # If account_id provided, save cookies to account in DB
        if account_id:
            account = self.db.get_account(account_id)
            if account:
                account.session_cookies = netscape_cookies
                self.db.save_account(account)
                logger.info(f"Updated session cookies for account {account_id}")

        return netscape_cookies
