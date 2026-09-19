"""Multi-worker process orchestrator and runner."""

from __future__ import annotations
import logging
import threading
import time
from typing import List

from clipforge.core.db import Database
from clipforge.core.queue import JobQueue
from clipforge.workers.base import BaseWorker
from clipforge.workers.downloader import DownloaderWorker
from clipforge.workers.publisher import PublisherWorker
from clipforge.workers.render import RenderWorker

logger = logging.getLogger(__name__)


class WorkerRunner:
    def __init__(self, db: Database, queue: JobQueue):
        self.db = db
        self.queue = queue
        self.workers: List[BaseWorker] = []
        self.threads: List[threading.Thread] = []
        self._running = False

    def add_worker(self, worker: BaseWorker) -> None:
        self.workers.append(worker)

    def start_all(self, poll_interval: float = 2.0) -> None:
        """Start standard set of workers (Downloader + Publisher + Render)."""
        downloader = DownloaderWorker(self.db, self.queue, poll_interval=poll_interval)
        publisher = PublisherWorker(self.db, self.queue, poll_interval=poll_interval)
        render_worker = RenderWorker(self.db, self.queue, poll_interval=poll_interval)

        self.workers = [downloader, publisher, render_worker]
        self._running = True

        for worker in self.workers:
            t = threading.Thread(target=worker.run, daemon=True, name=worker.worker_id)
            self.threads.append(t)
            t.start()

        logger.info(f"Started {len(self.workers)} workers in background threads.")

        try:
            while self._running:
                # Periodically clean stale job locks (e.g. every 60s)
                time.sleep(10.0)
                stale_count = self.queue.clear_stale_locks(timeout_seconds=1800)
                if stale_count > 0:
                    logger.warning(f"Cleared {stale_count} stale job locks.")
        except KeyboardInterrupt:
            logger.info("Shutdown requested. Stopping all workers...")
            self.stop()

    def stop(self) -> None:
        self._running = False
        for worker in self.workers:
            worker.stop()
        for t in self.threads:
            t.join(timeout=5.0)
        logger.info("All workers stopped.")
