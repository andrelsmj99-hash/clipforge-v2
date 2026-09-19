"""Tests for SQLite Job Queue concurrency and retry mechanism."""

import pytest
from clipforge.core.db import Database
from clipforge.core.models import JobStatus, JobType
from clipforge.core.queue import JobQueue


@pytest.fixture
def queue(tmp_path):
    db = Database(db_path=tmp_path / "test_queue.db")
    return JobQueue(db)


def test_enqueue_and_acquire(queue):
    job = queue.enqueue(
        job_type=JobType.DOWNLOAD,
        payload={"url": "https://www.youtube.com/watch?v=123"},
        max_attempts=3,
    )
    assert job.status == JobStatus.QUEUED
    assert job.type == JobType.DOWNLOAD

    # Worker 1 acquires the job
    acquired = queue.acquire_next(worker_id="worker_1", job_types=[JobType.DOWNLOAD])
    assert acquired is not None
    assert acquired.id == job.id
    assert acquired.status == JobStatus.RUNNING
    assert acquired.locked_by == "worker_1"

    # Worker 2 tries to acquire but queue is now empty
    acquired_2 = queue.acquire_next(worker_id="worker_2", job_types=[JobType.DOWNLOAD])
    assert acquired_2 is None

    # Complete the job
    queue.complete(job.id, worker_id="worker_1")
    finished = queue.get_job(job.id)
    assert finished.status == JobStatus.COMPLETED
    assert finished.locked_by is None


def test_job_failure_and_retry(queue):
    job = queue.enqueue(
        job_type=JobType.PUBLISH,
        payload={"post_id": "p1"},
        max_attempts=2,
    )

    # Attempt 1: fails
    acq1 = queue.acquire_next("worker_1", job_types=[JobType.PUBLISH])
    assert acq1 is not None
    queue.fail(acq1.id, error_message="Network timeout", worker_id="worker_1", can_retry=True)

    requeued = queue.get_job(job.id)
    assert requeued.status == JobStatus.QUEUED
    assert requeued.attempts == 1
    assert requeued.error == "Network timeout"

    # Attempt 2: fails again (hits max_attempts=2)
    acq2 = queue.acquire_next("worker_1", job_types=[JobType.PUBLISH])
    assert acq2 is not None
    queue.fail(acq2.id, error_message="Network timeout 2", worker_id="worker_1", can_retry=True)

    failed = queue.get_job(job.id)
    assert failed.status == JobStatus.FAILED
    assert failed.attempts == 2
