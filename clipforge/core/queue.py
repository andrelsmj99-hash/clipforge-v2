"""Transactional Job Queue built on top of SQLite."""

from __future__ import annotations
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from clipforge.core.db import Database, _now_iso, _parse_iso
from clipforge.core.models import Job, JobStatus, JobType


class JobQueue:
    def __init__(self, db: Database):
        self.db = db

    def enqueue(
        self,
        job_type: JobType,
        payload: Dict[str, Any],
        max_attempts: int = 3,
        job_id: Optional[str] = None,
    ) -> Job:
        """Add a new job to the queue."""
        now = _now_iso()
        j_id = job_id or f"job_{uuid.uuid4().hex[:12]}"
        payload_json = json.dumps(payload)
        type_str = job_type.value if isinstance(job_type, JobType) else job_type

        with self.db.transaction() as cur:
            cur.execute(
                """
                INSERT INTO jobs (id, type, payload, status, attempts, max_attempts, created_at, updated_at)
                VALUES (?, ?, ?, 'queued', 0, ?, ?, ?)
                """,
                (j_id, type_str, payload_json, max_attempts, now, now),
            )

        return Job(
            id=j_id,
            type=JobType(type_str),
            payload=payload,
            status=JobStatus.QUEUED,
            attempts=0,
            max_attempts=max_attempts,
            created_at=_parse_iso(now),
            updated_at=_parse_iso(now),
        )

    def acquire_next(
        self,
        worker_id: str,
        job_types: Optional[List[JobType]] = None,
    ) -> Optional[Job]:
        """Atomically lock and return the next available job for a worker process."""
        now = _now_iso()
        with self.db.transaction() as cur:
            # Build filter for specific job types if specified
            type_filter = ""
            params: List[Any] = []
            if job_types:
                placeholders = ",".join("?" for _ in job_types)
                type_filter = f"AND type IN ({placeholders})"
                params.extend([jt.value if isinstance(jt, JobType) else jt for jt in job_types])

            # Select candidate job
            query = f"""
                SELECT id FROM jobs
                WHERE status = 'queued' {type_filter}
                ORDER BY created_at ASC
                LIMIT 1
            """
            cur.execute(query, params)
            row = cur.fetchone()
            if not row:
                return None

            job_id = row["id"]

            # Atomically lock candidate
            cur.execute(
                """
                UPDATE jobs
                SET status = 'running',
                    locked_by = ?,
                    locked_at = ?,
                    updated_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (worker_id, now, now, job_id),
            )

            # Retrieve locked job
            cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            locked_row = cur.fetchone()
            if not locked_row:
                return None

            return self._row_to_job(locked_row)

    def complete(self, job_id: str, worker_id: Optional[str] = None) -> None:
        """Mark a job as successfully completed."""
        now = _now_iso()
        with self.db.transaction() as cur:
            cur.execute(
                """
                UPDATE jobs
                SET status = 'completed',
                    locked_by = NULL,
                    locked_at = NULL,
                    error = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, job_id),
            )

    def fail(
        self,
        job_id: str,
        error_message: str,
        worker_id: Optional[str] = None,
        can_retry: bool = True,
    ) -> None:
        """Mark a job as failed, incrementing attempts or requeueing if retries remain."""
        now = _now_iso()
        with self.db.transaction() as cur:
            cur.execute("SELECT attempts, max_attempts FROM jobs WHERE id = ?", (job_id,))
            row = cur.fetchone()
            if not row:
                return

            new_attempts = row["attempts"] + 1
            max_attempts = row["max_attempts"]

            if can_retry and new_attempts < max_attempts:
                new_status = "queued"  # Requeue for retry
            else:
                new_status = "failed"  # Reached max retries or non-retryable

            cur.execute(
                """
                UPDATE jobs
                SET status = ?,
                    attempts = ?,
                    error = ?,
                    locked_by = NULL,
                    locked_at = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (new_status, new_attempts, error_message, now, job_id),
            )

    def get_job(self, job_id: str) -> Optional[Job]:
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                return None
            return self._row_to_job(row)

    def list_jobs(
        self,
        status: Optional[JobStatus] = None,
        job_type: Optional[JobType] = None,
        limit: int = 50,
    ) -> List[Job]:
        query = "SELECT * FROM jobs WHERE 1=1"
        params: List[Any] = []
        if status:
            query += " AND status = ?"
            params.append(status.value if isinstance(status, JobStatus) else status)
        if job_type:
            query += " AND type = ?"
            params.append(job_type.value if isinstance(job_type, JobType) else job_type)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self.db.get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_job(r) for r in rows]

    def clear_stale_locks(self, timeout_seconds: int = 1800) -> int:
        """Release locks from jobs that have been stuck in 'running' longer than timeout_seconds."""
        now = _now_iso()
        with self.db.transaction() as cur:
            cur.execute(
                """
                UPDATE jobs
                SET status = 'queued',
                    locked_by = NULL,
                    locked_at = NULL,
                    updated_at = ?
                WHERE status = 'running'
                  AND locked_at IS NOT NULL
                  AND (strftime('%s', 'now') - strftime('%s', locked_at)) > ?
                """,
                (now, timeout_seconds),
            )
            return cur.rowcount

    def _row_to_job(self, row: Any) -> Job:
        return Job(
            id=row["id"],
            type=JobType(row["type"]) if row["type"] in JobType._value2member_map_ else JobType.DOWNLOAD,
            payload=json.loads(row["payload"]) if row["payload"] else {},
            status=JobStatus(row["status"]) if row["status"] in JobStatus._value2member_map_ else JobStatus.QUEUED,
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            error=row["error"],
            locked_by=row["locked_by"],
            locked_at=_parse_iso(row["locked_at"]),
            created_at=_parse_iso(row["created_at"]),
            updated_at=_parse_iso(row["updated_at"]),
        )
