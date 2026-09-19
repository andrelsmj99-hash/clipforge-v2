"""Base interface for all platform connectors."""

from __future__ import annotations
import abc
from typing import Any, Dict, Optional

from clipforge.core.models import Account, Platform, Post


class BaseConnector(abc.ABC):
    def __init__(self, account_id: str, platform: Platform):
        self.account_id = account_id
        self.platform = platform

    @abc.abstractmethod
    def is_connected(self) -> bool:
        """Check if connector has valid and active credentials/session."""
        pass

    @abc.abstractmethod
    def get_account_profile(self) -> Dict[str, Any]:
        """Fetch current profile information (channel/page name, follower count, avatar)."""
        pass

    @abc.abstractmethod
    def publish_video(self, post: Post) -> str:
        """
        Publish or schedule a video post.
        Returns the external platform post/video ID.
        """
        pass
