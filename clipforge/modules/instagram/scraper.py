"""Instagram profile scraper (list videos without downloading)."""

from __future__ import annotations
from clipforge.modules.common.profile_scraper import ProfileScraper


class InstagramScraper(ProfileScraper):
    platform_name = "instagram"

    @staticmethod
    def normalize_profile_url(handle_or_url: str) -> str:
        h = handle_or_url.strip()
        if h.startswith("http://") or h.startswith("https://"):
            return h
        h = h.lstrip("@")
        return f"https://www.instagram.com/{h}/"
