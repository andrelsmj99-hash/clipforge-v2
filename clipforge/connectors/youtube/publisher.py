"""YouTube Publisher implementing resumable video upload with native scheduling (publishAt)."""

from __future__ import annotations
import http.client
import logging
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import googleapiclient.errors
import httplib2
from googleapiclient.http import MediaFileUpload

from clipforge.connectors.base import BaseConnector
from clipforge.connectors.youtube.auth import YouTubeAuthManager
from clipforge.core.db import Database
from clipforge.core.models import Platform, Post, PostStatus

logger = logging.getLogger(__name__)

# Retry configuration for resumable uploads
RETRIABLE_EXCEPTIONS = (
    httplib2.HttpLib2Error,
    IOError,
    http.client.NotConnected,
    http.client.IncompleteRead,
    http.client.ImproperConnectionState,
    http.client.CannotSendRequest,
    http.client.CannotSendHeader,
    http.client.ResponseNotReady,
    http.client.BadStatusLine,
)
RETRIABLE_STATUS_CODES = [500, 502, 503, 504]
MAX_RETRIES = 5


class YouTubePublishError(Exception):
    """Base error for YouTube upload failures."""
    pass


class YouTubePublisher(BaseConnector):
    def __init__(self, db: Database, account_id: str):
        super().__init__(account_id=account_id, platform=Platform.YOUTUBE)
        self.db = db
        self.auth_manager = YouTubeAuthManager(db)

    def is_connected(self) -> bool:
        account = self.db.get_account(self.account_id)
        if not account or not account.credentials_json:
            return False
        health = self.auth_manager.check_token_health(account)
        return not health["is_expired"]

    def get_account_profile(self) -> Dict[str, Any]:
        service = self.auth_manager.get_service(self.account_id)
        resp = service.channels().list(mine=True, part="snippet,statistics").execute()
        items = resp.get("items", [])
        if not items:
            return {}
        info = items[0]
        snippet = info.get("snippet", {})
        stats = info.get("statistics", {})
        return {
            "channel_id": info.get("id"),
            "title": snippet.get("title"),
            "description": snippet.get("description"),
            "subscriber_count": stats.get("subscriberCount"),
            "video_count": stats.get("videoCount"),
            "view_count": stats.get("viewCount"),
        }

    def publish_video(
        self,
        post: Post,
        video_path: Optional[str] = None,
        category_id: str = "22",
        made_for_kids: bool = False,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> str:
        """
        Upload a video to YouTube with native scheduling (publishAt).
        When scheduled_at is in the future, privacyStatus is set to 'private'
        and YouTube automatically releases the video publicly at publishAt.
        """
        file_to_upload = Path(video_path or "")
        if not file_to_upload.exists():
            raise FileNotFoundError(f"Video file to publish does not exist: {file_to_upload}")

        service = self.auth_manager.get_service(self.account_id)

        now = datetime.now(timezone.utc)
        scheduled_dt = post.scheduled_at

        # Determine scheduling and privacy status
        is_future_scheduled = scheduled_dt > now

        body: Dict[str, Any] = {
            "snippet": {
                "title": post.title or file_to_upload.stem,
                "description": post.caption or "",
                "tags": post.tags or [],
                "categoryId": category_id,
            },
            "status": {
                "selfDeclaredMadeForKids": made_for_kids,
            },
        }

        if is_future_scheduled:
            # YouTube requires privacyStatus to be 'private' when publishAt is set
            body["status"]["privacyStatus"] = "private"
            body["status"]["publishAt"] = scheduled_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            logger.info(f"Setting YouTube native schedule publishAt: {body['status']['publishAt']}")
        else:
            body["status"]["privacyStatus"] = "public"

        # Initialize resumable media upload (1MB chunk size)
        media = MediaFileUpload(
            str(file_to_upload),
            mimetype="video/*",
            resumable=True,
            chunksize=1024 * 1024,
        )

        request = service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        logger.info(f"Starting resumable YouTube upload for file {file_to_upload.name}...")
        response = None
        error = None
        retry = 0

        while response is None:
            try:
                status, response = request.next_chunk()
                if status:
                    progress = float(status.progress())
                    logger.info(f"YouTube Upload Progress: {int(progress * 100)}%")
                    if progress_callback:
                        progress_callback(progress)
            except googleapiclient.errors.HttpError as e:
                if e.resp.status in RETRIABLE_STATUS_CODES:
                    error = f"A retriable HTTP error {e.resp.status} occurred: {e.content}"
                else:
                    raise YouTubePublishError(f"HTTP Error during YouTube upload: {e}") from e
            except RETRIABLE_EXCEPTIONS as e:
                error = f"A retriable connection error occurred: {e}"

            if error:
                retry += 1
                if retry > MAX_RETRIES:
                    raise YouTubePublishError(f"Maximum upload retries exceeded. Last error: {error}")
                max_sleep = 2 ** retry
                sleep_seconds = random.random() * max_sleep
                logger.warning(f"{error}. Retrying in {sleep_seconds:.1f} seconds...")
                time.sleep(sleep_seconds)
                error = None

        video_id = response.get("id")
        if not video_id:
            raise YouTubePublishError(f"YouTube upload finished but returned no video id: {response}")

        logger.info(f"YouTube upload successful! Video ID: {video_id}")
        return video_id
