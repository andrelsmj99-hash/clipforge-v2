"""Publisher worker process consuming publish jobs."""

from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import List, Optional

from clipforge.connectors.instagram.publisher import InstagramPublisher
from clipforge.connectors.tiktok.publisher import TikTokPublisher
from clipforge.connectors.youtube.publisher import YouTubePublisher
from clipforge.core.db import Database
from clipforge.core.models import Job, JobType, Platform, Post, PostStatus
from clipforge.core.queue import JobQueue
from clipforge.workers.base import BaseWorker

logger = logging.getLogger(__name__)


class PublisherWorker(BaseWorker):
    def __init__(
        self,
        db: Database,
        queue: JobQueue,
        worker_id: Optional[str] = None,
        poll_interval: float = 2.0,
    ):
        super().__init__(db, queue, worker_id=worker_id, poll_interval=poll_interval)

    def supported_job_types(self) -> List[JobType]:
        return [JobType.PUBLISH]

    def handle_job(self, job: Job) -> None:
        payload = job.payload
        post_id = payload.get("post_id")
        account_id = payload.get("account_id")
        platform_str = payload.get("platform", "youtube")
        video_path = payload.get("video_path")
        made_for_kids = payload.get("made_for_kids", False)

        if not post_id or not account_id or not video_path:
            raise ValueError(f"Invalid publish job payload (missing post_id, account_id, or video_path): {payload}")

        post = self.db.get_post(post_id)
        if not post:
            raise ValueError(f"Post {post_id} not found in database.")

        platform = Platform(platform_str)
        logger.info(f"Processing publish job for post {post_id} on {platform.value}")

        try:
            if platform == Platform.YOUTUBE:
                publisher = YouTubePublisher(self.db, account_id=account_id)
                external_id = publisher.publish_video(
                    post=post,
                    video_path=video_path,
                    made_for_kids=made_for_kids,
                )
            elif platform == Platform.TIKTOK:
                publisher = TikTokPublisher(self.db, account_id=account_id)
                external_id = publisher.publish_video(
                    post=post,
                    video_path=video_path,
                )
            elif platform == Platform.INSTAGRAM:
                publisher = InstagramPublisher(self.db, account_id=account_id)
                external_id = publisher.publish_video(
                    post=post,
                    video_path=video_path,
                )
            else:
                raise ValueError(f"Unsupported platform: {platform.value}")

            now = datetime.now(timezone.utc)
            post.external_post_id = external_id
            post.error_message = None

            if post.scheduled_at > now:
                post.status = PostStatus.SCHEDULED
                logger.info(f"Post {post_id} natively scheduled on {platform.value} (ID: {external_id})")
            else:
                post.status = PostStatus.PUBLISHED
                post.published_at = now
                logger.info(f"Post {post_id} published immediately on {platform.value} (ID: {external_id})")

            self.db.save_post(post)

        except Exception as e:
            post.status = PostStatus.FAILED
            post.error_message = str(e)
            self.db.save_post(post)
            raise
