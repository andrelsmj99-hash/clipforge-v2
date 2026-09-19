"""
Shared UI State Manager for Clip Forge V2 Desktop UI.
Connects directly to Database and JobQueue and coordinates background workers.
"""

from __future__ import annotations
import logging
import threading
from typing import Any, Dict, List, Optional

from clipforge.core.db import Database
from clipforge.core.models import (
    Account,
    AccountStatus,
    Job,
    JobStatus,
    JobType,
    Platform,
    Post,
    PostBatch,
    PostStatus,
    Render,
    Template,
    Video,
    VideoKind,
    VideoStatus,
)
from clipforge.core.queue import JobQueue
from clipforge.workers.runner import WorkerRunner

logger = logging.getLogger(__name__)


class UIState:
    """Manages application-wide data, queries, and background processes for the UI."""

    def __init__(self, db: Optional[Database] = None, queue: Optional[JobQueue] = None):
        self.db = db or Database()
        self.queue = queue or JobQueue(self.db)
        self.runner: Optional[WorkerRunner] = None
        self.runner_thread: Optional[threading.Thread] = None

    # --------------------------------------------------------------------------
    # Aggregated Stats
    # --------------------------------------------------------------------------
    def get_stats(self) -> Dict[str, Any]:
        with self.db.get_connection() as conn:
            total_videos = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
            total_shorts = conn.execute("SELECT COUNT(*) FROM videos WHERE kind = 'short'").fetchone()[0]
            
            total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            queued_jobs = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'queued'").fetchone()[0]
            running_jobs = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'running'").fetchone()[0]
            failed_jobs = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'failed'").fetchone()[0]
            
            total_templates = conn.execute("SELECT COUNT(*) FROM templates").fetchone()[0]
            mapped_templates = conn.execute("SELECT COUNT(*) FROM templates WHERE placeholder_map IS NOT NULL").fetchone()[0]
            
            total_renders = conn.execute("SELECT COUNT(*) FROM renders").fetchone()[0]
            total_batches = conn.execute("SELECT COUNT(*) FROM post_batches").fetchone()[0]
            total_posts = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
            scheduled_posts = conn.execute("SELECT COUNT(*) FROM posts WHERE status = 'scheduled'").fetchone()[0]
            
            connected_accounts = conn.execute("SELECT COUNT(*) FROM accounts WHERE status = 'connected'").fetchone()[0]

        return {
            "total_videos": total_videos,
            "total_shorts": total_shorts,
            "total_jobs": total_jobs,
            "queued_jobs": queued_jobs,
            "running_jobs": running_jobs,
            "failed_jobs": failed_jobs,
            "total_templates": total_templates,
            "mapped_templates": mapped_templates,
            "total_renders": total_renders,
            "total_batches": total_batches,
            "total_posts": total_posts,
            "scheduled_posts": scheduled_posts,
            "connected_accounts": connected_accounts,
            "workers_running": self.is_workers_running(),
        }

    # --------------------------------------------------------------------------
    # Query Helpers
    # --------------------------------------------------------------------------
    def get_recent_jobs(self, limit: int = 30) -> List[Job]:
        return self.queue.list_jobs(limit=limit)

    def get_accounts(self) -> List[Account]:
        return self.db.list_accounts()

    def get_videos(self, kind: Optional[VideoKind] = None, limit: int = 50) -> List[Video]:
        videos = self.db.list_videos(kind=kind)
        return videos[:limit]

    def get_templates(self) -> List[Template]:
        return self.db.list_templates()

    def get_renders(self, limit: int = 50) -> List[Render]:
        return self.db.list_renders(limit=limit)

    def get_batches(self) -> List[PostBatch]:
        return self.db.list_post_batches()

    def get_posts(self, batch_id: Optional[str] = None, limit: int = 50) -> List[Post]:
        posts = self.db.list_posts(batch_id=batch_id)
        return posts[:limit]

    # --------------------------------------------------------------------------
    # Worker Runner Control
    # --------------------------------------------------------------------------
    def start_workers(self, poll_interval: float = 2.0) -> bool:
        """Start the background WorkerRunner daemon thread."""
        if self.is_workers_running():
            return True

        self.runner = WorkerRunner(self.db, self.queue)
        self.runner_thread = threading.Thread(
            target=self.runner.start_all,
            kwargs={"poll_interval": poll_interval},
            daemon=True,
            name="ClipForgeUIWorkerDaemon",
        )
        self.runner_thread.start()
        logger.info("UI initiated background WorkerRunner daemon.")
        return True

    def stop_workers(self) -> bool:
        """Stop running workers."""
        if self.runner:
            self.runner.stop()
            self.runner = None
            self.runner_thread = None
            logger.info("UI stopped background WorkerRunner daemon.")
        return True

    def is_workers_running(self) -> bool:
        return self.runner is not None and self.runner._running
