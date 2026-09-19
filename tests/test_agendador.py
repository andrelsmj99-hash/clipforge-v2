"""
Tests for the Agendador module (planejamento seção 3.5):
- Postagem em massa: N vídeos, mesma legenda (com {n}/{total}), intervalo definido
- YouTube: publish job enfileirado imediatamente (agendamento nativo via publishAt)
- TikTok (sem automação de agendamento nativo implementada ainda): post fica SCHEDULED, sem job — liberado pelo DueScanner
- DueScanner: libera só posts vencidos, e não duplica se chamado duas vezes
"""

from __future__ import annotations
from datetime import datetime, timedelta, timezone
import pytest

from clipforge.core.db import Database
from clipforge.core.models import (
    Account,
    AccountStatus,
    JobStatus,
    JobType,
    Platform,
    Post,
    PostStatus,
    Video,
    VideoKind,
    VideoStatus,
)
from clipforge.core.queue import JobQueue
from clipforge.modules.agendador.due_scanner import DueScanner
from clipforge.modules.agendador.scheduler import BatchScheduler


@pytest.fixture
def env(tmp_path):
    db = Database(db_path=tmp_path / "test_agendador.db")
    jq = JobQueue(db)

    db.save_account(Account(id="acc1", platform=Platform.YOUTUBE, status=AccountStatus.CONNECTED))
    db.save_account(Account(id="acc_tiktok", platform=Platform.TIKTOK, status=AccountStatus.CONNECTED))

    video_ids = []
    for i in range(3):
        vid = f"vid_{i}"
        db.save_video(Video(
            id=vid,
            source_url=f"https://youtube.com/watch?v={i}",
            local_path=f"/fake/path/{i}.mp4",
            kind=VideoKind.SHORT,
            status=VideoStatus.DOWNLOADED,
        ))
        video_ids.append(vid)

    return db, jq, video_ids


def test_create_batch_computes_scheduled_at_with_interval(env):
    db, jq, video_ids = env
    scheduler = BatchScheduler(db, jq)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    batch = scheduler.create_batch(
        video_ids=video_ids,
        account_id="acc1",
        platform=Platform.YOUTUBE,
        caption_template="Parte {n}/{total}",
        interval_seconds=3600,
        start_at=start,
    )

    posts = db.list_posts(batch_id=batch.id)
    assert len(posts) == 3
    posts.sort(key=lambda p: p.scheduled_at)

    assert posts[0].scheduled_at == start
    assert posts[1].scheduled_at == start + timedelta(hours=1)
    assert posts[2].scheduled_at == start + timedelta(hours=2)

    assert posts[0].caption == "Parte 1/3"
    assert posts[2].caption == "Parte 3/3"


def test_create_batch_rejects_video_not_downloaded(env):
    db, jq, video_ids = env
    db.save_video(Video(id="not_ready", source_url="https://x.com/v", status=VideoStatus.PENDING))
    scheduler = BatchScheduler(db, jq)

    with pytest.raises(ValueError):
        scheduler.create_batch(
            video_ids=["not_ready"],
            account_id="acc1",
            platform=Platform.YOUTUBE,
            caption_template="oi",
        )


def test_youtube_batch_enqueues_publish_jobs_immediately(env):
    db, jq, video_ids = env
    scheduler = BatchScheduler(db, jq)

    scheduler.create_batch(
        video_ids=video_ids,
        account_id="acc1",
        platform=Platform.YOUTUBE,
        caption_template="Post {n}",
        interval_seconds=1800,
        start_at=datetime.now(timezone.utc) + timedelta(days=1),
    )

    pending_jobs = [j for j in jq.list_jobs() if j.type == JobType.PUBLISH]
    assert len(pending_jobs) == 3
    assert all(j.status == JobStatus.QUEUED for j in pending_jobs)

    posts = db.list_posts(account_id="acc1")
    assert all(p.status == PostStatus.SCHEDULED for p in posts)


def test_tiktok_batch_defers_to_due_scanner(env):
    db, jq, video_ids = env
    scheduler = BatchScheduler(db, jq)

    scheduler.create_batch(
        video_ids=video_ids,
        account_id="acc_tiktok",
        platform=Platform.TIKTOK,
        caption_template="Post {n}",
        interval_seconds=1800,
        start_at=datetime.now(timezone.utc) + timedelta(days=1),
    )

    # Sem automação de agendamento nativo pro TikTok ainda: nenhum job deve ser criado.
    pending_jobs = [j for j in jq.list_jobs() if j.type == JobType.PUBLISH]
    assert len(pending_jobs) == 0

    posts = db.list_posts(account_id="acc_tiktok")
    assert all(p.status == PostStatus.SCHEDULED for p in posts)


def test_due_scanner_enqueues_only_posts_whose_time_passed(env):
    db, jq, video_ids = env

    past_post = Post(
        id="post_past",
        video_id=video_ids[0],
        account_id="acc_tiktok",
        platform=Platform.TIKTOK,
        caption="já venceu",
        scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        status=PostStatus.SCHEDULED,
    )
    future_post = Post(
        id="post_future",
        video_id=video_ids[1],
        account_id="acc_tiktok",
        platform=Platform.TIKTOK,
        caption="ainda não venceu",
        scheduled_at=datetime.now(timezone.utc) + timedelta(hours=5),
        status=PostStatus.SCHEDULED,
    )
    db.save_post(past_post)
    db.save_post(future_post)

    scanner = DueScanner(db, jq)
    count = scanner.run_once()

    assert count == 1
    jobs = [j for j in jq.list_jobs() if j.type == JobType.PUBLISH]
    assert len(jobs) == 1
    assert jobs[0].payload["post_id"] == "post_past"

    assert db.get_post("post_past").status == PostStatus.UPLOADING
    assert db.get_post("post_future").status == PostStatus.SCHEDULED


def test_due_scanner_does_not_double_enqueue_on_repeated_calls(env):
    db, jq, video_ids = env

    post = Post(
        id="post_once",
        video_id=video_ids[0],
        account_id="acc_tiktok",
        platform=Platform.TIKTOK,
        caption="oi",
        scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        status=PostStatus.SCHEDULED,
    )
    db.save_post(post)

    scanner = DueScanner(db, jq)
    first_count = scanner.run_once()
    second_count = scanner.run_once()

    assert first_count == 1
    assert second_count == 0  # já foi reivindicado (status virou UPLOADING)

    jobs = [j for j in jq.list_jobs() if j.type == JobType.PUBLISH]
    assert len(jobs) == 1


def test_due_scanner_fails_post_with_no_resolvable_video(env):
    db, jq, video_ids = env

    orphan_post = Post(
        id="post_orphan",
        account_id="acc_tiktok",
        platform=Platform.TIKTOK,
        caption="sem vídeo associado",
        scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        status=PostStatus.SCHEDULED,
    )
    db.save_post(orphan_post)

    scanner = DueScanner(db, jq)
    count = scanner.run_once()

    assert count == 0
    updated = db.get_post("post_orphan")
    assert updated.status == PostStatus.FAILED
    assert updated.error_message
