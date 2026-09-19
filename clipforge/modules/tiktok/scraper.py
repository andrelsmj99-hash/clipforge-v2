"""TikTok profile scraper (list videos without downloading)."""

from __future__ import annotations
from clipforge.modules.common.profile_scraper import ProfileScraper


class TikTokScraper(ProfileScraper):
    platform_name = "tiktok"

    @staticmethod
    def normalize_profile_url(handle_or_url: str) -> str:
        h = handle_or_url.strip()
        if h.startswith("http://") or h.startswith("https://"):
            return h
        if not h.startswith("@"):
            h = f"@{h}"
        return f"https://www.tiktok.com/{h}"
