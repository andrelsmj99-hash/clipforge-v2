"""
Render worker process consuming render jobs.
"""

from __future__ import annotations
import logging
from typing import List, Optional

from clipforge.core.db import Database
from clipforge.core.models import Job, JobType
from clipforge.core.queue import JobQueue
from clipforge.modules.editor.renderer import CanvaRenderer
from clipforge.workers.base import BaseWorker

logger = logging.getLogger(__name__)


class RenderWorker(BaseWorker):
    def __init__(
        self,
        db: Database,
        queue: JobQueue,
        worker_id: Optional[str] = None,
        poll_interval: float = 2.0,
        renderer: Optional[CanvaRenderer] = None,
    ):
        super().__init__(db, queue, worker_id=worker_id, poll_interval=poll_interval)
        self.renderer = renderer or CanvaRenderer(db)

    def supported_job_types(self) -> List[JobType]:
        return [JobType.RENDER]

    def handle_job(self, job: Job) -> None:
        payload = job.payload
        video_id = payload.get("video_id")
        template_id = payload.get("template_id")
        render_id = payload.get("render_id")

        if not video_id or not template_id:
            raise ValueError(f"Invalid render job payload (missing video_id or template_id): {payload}")

        logger.info(f"Processing render job {job.id} for video={video_id}, template={template_id}")
        self.renderer.render(video_id=video_id, template_id=template_id, render_id=render_id)
