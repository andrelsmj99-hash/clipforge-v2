"""
Generic profile/channel video listing via yt-dlp, for platforms without
YouTube's tab structure (TikTok, Instagram). Lists videos on a profile URL
without downloading them.
"""

from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional
import yt_dlp

from clipforge.core.models import VideoKind
from clipforge.modules.youtube.scraper import classify_video_kind

logger = logging.getLogger(__name__)


class ProfileScraper:
    """List videos on a profile URL (TikTok/Instagram) without downloading."""

    platform_name: str = "generic"

    def __init__(self, cookies_path: Optional[str] = None):
        self.cookies_path = cookies_path

    def list_profile_videos(
        self,
        profile_url: str,
        max_results: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        ydl_opts: Dict[str, Any] = {

            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "skip_download": True,
            "ignoreerrors": True,
            "playlist_items": f"1-{max_results}" if max_results else None,
        }
        if self.cookies_path:
            ydl_opts["cookiefile"] = self.cookies_path

        results: List[Dict[str, Any]] = []

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(profile_url, download=False)
            except Exception as e:
                logger.error(f"[{self.platform_name}] Error listing profile {profile_url}: {e}")
                raise

            if not info:
                return []

            entries = info.get("entries") or [info]
            for entry in entries:
                if not entry or not isinstance(entry, dict):
                    continue

                vid_id = entry.get("id")
                if not vid_id:
                    continue

                url = entry.get("url") or entry.get("webpage_url")
                duration = entry.get("duration")
                width = entry.get("width")
                height = entry.get("height")

                results.append({
                    "id": vid_id,
                    "title": entry.get("title") or f"{self.platform_name}_{vid_id}",
                    "url": url,
                    "duration_seconds": duration,
                    "width": width,
                    "height": height,
                    "kind": classify_video_kind(duration=duration, width=width, height=height, url=url),
                    "channel_name": entry.get("uploader") or info.get("uploader"),
                    "channel_id": entry.get("uploader_id") or info.get("uploader_id"),
                })

                if max_results and len(results) >= max_results:
                    break

        return results

    def list_user_videos(
        self,
        profile_url: str,
        max_results: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Alias for list_profile_videos for backward compatibility."""
        return self.list_profile_videos(profile_url, max_results=max_results)
