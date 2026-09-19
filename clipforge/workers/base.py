"""Base Worker class handling polling, locking, retries, and graceful termination."""

from __future__ import annotations
import abc
import logging
import signal
import sys
import time
import uuid
from typing import List, Optional

from clipforge.core.db import Database
from clipforge.core.models import Job, JobType
from clipforge.core.queue import JobQueue

logger = logging.getLogger(__name__)


class BaseWorker(abc.ABC):
    def __init__(
        self,
        db: Database,
        queue: JobQueue,
        worker_id: Optional[str] = None,
        poll_interval: float = 2.0,
    ):
        self.db = db
        self.queue = queue
        self.worker_id = worker_id or f"worker_{self.__class__.__name__.lower()}_{uuid.uuid4().hex[:6]}"
        self.poll_interval = poll_interval
        self._running = False
        self._setup_signals()

    def _setup_signals(self) -> None:
        """Register signal handlers for graceful shutdown."""
        try:
            signal.signal(signal.SIGINT, self._handle_exit)
            signal.signal(signal.SIGTERM, self._handle_exit)
        except Exception:
            # On some platforms / threads, signal handlers might not be assignable
            pass

    def _handle_exit(self, signum: int, frame: Any) -> None:
        logger.info(f"[{self.worker_id}] Received shutdown signal. Stopping...")
        self.stop()

    def stop(self) -> None:
        self._running = False

    @abc.abstractmethod
    def supported_job_types(self) -> List[JobType]:
        """Return the list of job types this worker is responsible for."""
        pass

    @abc.abstractmethod
    def handle_job(self, job: Job) -> None:
        """Process a single job. Raise an exception on failure."""
        pass

    def run_once(self) -> bool:
        """
        Poll and execute a single job if available.
        Returns True if a job was processed, False otherwise.
        """
        job = self.queue.acquire_next(
            worker_id=self.worker_id,
            job_types=self.supported_job_types(),
        )
        if not job:
            return False

        logger.info(f"[{self.worker_id}] Starting job {job.id} (type={job.type.value})")
        try:
            self.handle_job(job)
            self.queue.complete(job.id, worker_id=self.worker_id)
            logger.info(f"[{self.worker_id}] Successfully finished job {job.id}")
        except Exception as e:
            logger.error(f"[{self.worker_id}] Error executing job {job.id}: {e}", exc_info=True)
            self.queue.fail(job.id, error_message=str(e), worker_id=self.worker_id)

        return True

    def run(self) -> None:
        """Main worker loop."""
        self._running = True
        logger.info(f"[{self.worker_id}] Worker started. Listening for jobs: {[jt.value for jt in self.supported_job_types()]}")

        while self._running:
            try:
                processed = self.run_once()
                if not processed:
                    time.sleep(self.poll_interval)
            except Exception as e:
                logger.error(f"[{self.worker_id}] Worker loop error: {e}", exc_info=True)
                time.sleep(self.poll_interval)

        logger.info(f"[{self.worker_id}] Worker stopped cleanly.")
