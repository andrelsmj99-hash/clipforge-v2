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


def test_canva_renderer_fails_on_missing_download_url(env):
    db, _, v, t, tmp_path = env
    renders_dir = tmp_path / "renders"
    renderer = CanvaRenderer(db=db, renders_dir=renders_dir)

    with patch.object(renderer.dev_server, "is_healthy", return_value=True):
        with patch("clipforge.modules.editor.renderer.sync_playwright") as mock_pw:
            mock_context = MagicMock()
            mock_page = MagicMock()
            mock_pw.return_value.__enter__.return_value.chromium.launch_persistent_context.return_value = mock_context
            renderer.session_manager.launch_context = MagicMock(return_value=mock_context)
            mock_context.pages = [mock_page]

            # When page.evaluate is called, trigger completion without downloadUrl
            def mock_evaluate(script, *args):
                # Simulate Canva app message without downloadUrl
                for call in mock_page.expose_function.call_args_list:
                    name, fn = call[0]
                    if name == "onClipforgeRenderComplete":
                        fn({"response": {}})

            mock_page.evaluate.side_effect = mock_evaluate

            with pytest.raises(RuntimeError, match="did not contain a valid download URL"):
                renderer.render(video_id=v.id, template_id=t.id, render_id="r_no_url", timeout_seconds=5)

            # Check DB record is FAILED
            render_in_db = db.get_render("r_no_url")
            assert render_in_db is not None
            assert render_in_db.status == RenderStatus.FAILED

            # Ensure no 0-byte file was left on disk
            out_file = renders_dir / "r_no_url.mp4"
            assert not out_file.exists()


def test_canva_renderer_fails_on_zero_byte_download(env):
    db, _, v, t, tmp_path = env
    renders_dir = tmp_path / "renders"
    renderer = CanvaRenderer(db=db, renders_dir=renders_dir)

    with patch.object(renderer.dev_server, "is_healthy", return_value=True):
        with patch("clipforge.modules.editor.renderer.sync_playwright") as mock_pw:
            mock_context = MagicMock()
            mock_page = MagicMock()
            renderer.session_manager.launch_context = MagicMock(return_value=mock_context)
            mock_context.pages = [mock_page]

            def mock_evaluate(script, *args):
                for call in mock_page.expose_function.call_args_list:
                    name, fn = call[0]
                    if name == "onClipforgeRenderComplete":
                        fn({"response": {"downloadUrl": "https://export.canva.com/empty.mp4"}})

            mock_page.evaluate.side_effect = mock_evaluate

            # Simulate urlretrieve writing an empty file
            def mock_urlretrieve(url, path):
                open(path, "wb").close()

            with patch("urllib.request.urlretrieve", side_effect=mock_urlretrieve):
                with pytest.raises(RuntimeError, match="empty \\(0 bytes\\)"):
                    renderer.render(video_id=v.id, template_id=t.id, render_id="r_zero_byte", timeout_seconds=5)

            render_in_db = db.get_render("r_zero_byte")
            assert render_in_db is not None
            assert render_in_db.status == RenderStatus.FAILED
            assert not (renders_dir / "r_zero_byte.mp4").exists()


def test_canva_renderer_succeeds_with_valid_download(env):
    db, _, v, t, tmp_path = env
    renders_dir = tmp_path / "renders"
    renderer = CanvaRenderer(db=db, renders_dir=renders_dir)

    with patch.object(renderer.dev_server, "is_healthy", return_value=True):
        with patch("clipforge.modules.editor.renderer.sync_playwright") as mock_pw:
            mock_context = MagicMock()
            mock_page = MagicMock()
            renderer.session_manager.launch_context = MagicMock(return_value=mock_context)
            mock_context.pages = [mock_page]

            def mock_evaluate(script, *args):
                for call in mock_page.expose_function.call_args_list:
                    name, fn = call[0]
                    if name == "onClipforgeRenderComplete":
                        fn({"response": {"downloadUrl": "https://export.canva.com/video123.mp4"}})

            mock_page.evaluate.side_effect = mock_evaluate

            # Simulate urlretrieve writing valid video bytes
            def mock_urlretrieve(url, path):
                with open(path, "wb") as f:
                    f.write(b"valid mp4 video content here")

            with patch("urllib.request.urlretrieve", side_effect=mock_urlretrieve):
                res = renderer.render(video_id=v.id, template_id=t.id, render_id="r_success", timeout_seconds=5)

            assert res.status == RenderStatus.COMPLETED
            out_file = renders_dir / "r_success.mp4"
            assert out_file.exists()
            assert out_file.read_bytes() == b"valid mp4 video content here"

            render_in_db = db.get_render("r_success")
            assert render_in_db is not None
            assert render_in_db.status == RenderStatus.COMPLETED
            assert render_in_db.output_path == str(out_file)

