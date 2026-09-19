"""
Tests for Editor Renderer and RenderWorker (Planejamento Seções 3.4 e 9).
"""

from __future__ import annotations
import urllib.request
import pytest
from unittest.mock import MagicMock, patch

from clipforge.core.db import Database
from clipforge.core.models import (
    JobStatus,
    JobType,
    Render,
    RenderStatus,
    Template,
    Video,
    VideoKind,
    VideoStatus,
)
from clipforge.core.queue import JobQueue
from clipforge.modules.editor.renderer import CanvaRenderer, LocalMediaServer
from clipforge.workers.render import RenderWorker


@pytest.fixture
def env(tmp_path):
    db = Database(db_path=tmp_path / "test_render.db")
    queue = JobQueue(db)

    # Create dummy video file
    video_file = tmp_path / "clip.mp4"
    video_file.write_bytes(b"dummy video content")

    v = Video(
        id="v1",
        source_url="https://youtube.com/watch?v=1",
        title="Test Clip",
        local_path=str(video_file),
        kind=VideoKind.SHORT,
        status=VideoStatus.DOWNLOADED,
    )
    db.save_video(v)

    t = Template(
        id="t1",
        canva_template_id="DAGX_test",
        canva_design_id="DAGX_test",
        name="Shorts Layout",
        template_url="https://canva.com/design/DAGX_test/edit",
        placeholder_map={"main_video": {"ref": "video_ref_1"}},
    )
    db.save_template(t)

    return db, queue, v, t, tmp_path


def test_render_worker_processes_job_successfully(env):
    db, queue, v, t, tmp_path = env

    mock_renderer = MagicMock(spec=CanvaRenderer)
    out_file = tmp_path / "renders" / "r1.mp4"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_bytes(b"final rendered mp4")

    mock_renderer.render.return_value = Render(
        id="r1",
        video_id=v.id,
        template_id=t.id,
        output_path=str(out_file),
        status=RenderStatus.COMPLETED,
    )

    worker = RenderWorker(db=db, queue=queue, renderer=mock_renderer)
    assert worker.supported_job_types() == [JobType.RENDER]

    job = queue.enqueue(
        job_type=JobType.RENDER,
        payload={"video_id": v.id, "template_id": t.id, "render_id": "r1"},
    )

    acquired = queue.acquire_next(worker_id="worker_render", job_types=[JobType.RENDER])
    assert acquired is not None

    worker.handle_job(acquired)
    queue.complete(acquired.id, worker_id="worker_render")

    mock_renderer.render.assert_called_once_with(
        video_id=v.id,
        template_id=t.id,
        render_id="r1",
    )

    completed_job = queue.get_job(job.id)
    assert completed_job.status == JobStatus.COMPLETED


def test_render_worker_fails_on_missing_payload_fields(env):
    db, queue, v, t, _ = env
    worker = RenderWorker(db=db, queue=queue, renderer=MagicMock())

    job = queue.enqueue(
        job_type=JobType.RENDER,
        payload={"video_id": v.id},  # Missing template_id
    )
    acquired = queue.acquire_next("w1", [JobType.RENDER])

    with pytest.raises(ValueError, match="missing video_id or template_id"):
        worker.handle_job(acquired)


def test_canva_renderer_validates_unmapped_template(env):
    db, _, v, _, tmp_path = env

    # Template without placeholder_map
    unmapped_t = Template(
        id="t_unmapped",
        name="Unmapped",
        template_url="https://canva.com/design/1/edit",
        placeholder_map=None,
    )
    db.save_template(unmapped_t)

    renderer = CanvaRenderer(db=db, renders_dir=tmp_path / "renders")
    with pytest.raises(ValueError, match="has not been mapped yet"):
        renderer.render(video_id=v.id, template_id=unmapped_t.id)


def test_canva_renderer_validates_missing_video(env):
    db, _, _, t, tmp_path = env
    renderer = CanvaRenderer(db=db, renders_dir=tmp_path / "renders")

    with pytest.raises(ValueError, match="not found"):
        renderer.render(video_id="nonexistent_video", template_id=t.id)


def test_local_media_server_serves_files_with_cors(tmp_path):
    media_file = tmp_path / "test.mp4"
    media_file.write_bytes(b"video data bytes")

    server = LocalMediaServer(directory=tmp_path, port=8003)
    server.start()

    try:
        url = "http://127.0.0.1:8003/test.mp4"
        with urllib.request.urlopen(url) as response:
            assert response.status == 200
            assert response.headers.get("Access-Control-Allow-Origin") == "*"
            content = response.read()
            assert content == b"video data bytes"
    finally:
        server.stop()
