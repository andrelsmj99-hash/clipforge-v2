"""
Tests for Clip Forge V2 Desktop UI (Flet) components, state, views, and CLI integration.
"""

from __future__ import annotations
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import pytest
from click.testing import CliRunner

from clipforge.cli import main as cli_main
from clipforge.core.db import Database
from clipforge.core.models import (
    Account,
    AccountStatus,
    JobStatus,
    JobType,
    Platform,
    Post,
    PostBatch,
    PostStatus,
    Render,
    RenderStatus,
    Template,
    Video,
    VideoKind,
    VideoStatus,
)
from clipforge.core.queue import JobQueue
from clipforge.ui.app import create_app
from clipforge.ui.components.header import AppHeader
from clipforge.ui.components.sidebar import AppSidebar
from clipforge.ui.components.stat_card import StatCard
from clipforge.ui.state import UIState
from clipforge.ui.views.accounts_view import AccountsView
from clipforge.ui.views.dashboard_view import DashboardView
from clipforge.ui.views.downloader_view import DownloaderView
from clipforge.ui.views.scheduler_view import SchedulerView
from clipforge.ui.views.templates_view import TemplatesView


@pytest.fixture
def env(tmp_path):
    db = Database(db_path=tmp_path / "test_ui.db")
    queue = JobQueue(db)

    # Seed an account
    acc = Account(
        id="acc_yt_1",
        platform=Platform.YOUTUBE,
        name="Test YouTube Channel",
        status=AccountStatus.CONNECTED,
    )
    db.save_account(acc)

    # Seed a video
    vid = Video(
        id="vid_short_1",
        source_url="https://www.youtube.com/shorts/abc12345",
        title="Epic Test Short",
        kind=VideoKind.SHORT,
        duration_seconds=42.0,
        status=VideoStatus.DOWNLOADED,
    )
    db.save_video(vid)

    # Seed a template
    tpl = Template(
        id="tpl_test_1",
        canva_design_id="DAGABC123",
        name="Shorts Dark Template",
        placeholder_map={"slot1": "video_element_xyz"},
    )
    db.save_template(tpl)

    # Seed a render
    rnd = Render(
        id="rnd_test_1",
        video_id=vid.id,
        template_id=tpl.id,
        output_path="data/renders/rnd_test_1.mp4",
        status=RenderStatus.COMPLETED,
    )
    db.save_render(rnd)

    # Seed a batch & post
    batch = PostBatch(
        id="batch_test_1",
        title="Batch 1",
        caption_template="Clip {n}/{total}",
        interval_seconds=3600,
        start_at=datetime.now(timezone.utc),
    )
    db.save_post_batch(batch)

    post = Post(
        id="post_test_1",
        batch_id=batch.id,
        video_id=vid.id,
        account_id=acc.id,
        platform=Platform.YOUTUBE,
        title="Post 1",
        caption="Clip 1/1",
        scheduled_at=datetime.now(timezone.utc) + timedelta(hours=2),
        status=PostStatus.SCHEDULED,
    )
    db.save_post(post)

    # Seed a job
    queue.enqueue(
        job_type=JobType.DOWNLOAD,
        payload={"video_id": vid.id, "source_url": vid.source_url},
    )

    state = UIState(db=db, queue=queue)
    return db, queue, state


def test_ui_state_stats(env):
    _, _, state = env
    stats = state.get_stats()

    assert stats["total_videos"] == 1
    assert stats["total_shorts"] == 1
    assert stats["total_jobs"] == 1
    assert stats["queued_jobs"] == 1
    assert stats["total_templates"] == 1
    assert stats["mapped_templates"] == 1
    assert stats["total_renders"] == 1
    assert stats["total_batches"] == 1
    assert stats["total_posts"] == 1
    assert stats["scheduled_posts"] == 1
    assert stats["connected_accounts"] == 1
    assert stats["workers_running"] is False


def test_ui_state_query_helpers(env):
    _, _, state = env

    jobs = state.get_recent_jobs()
    assert len(jobs) == 1
    assert jobs[0].type == JobType.DOWNLOAD

    accounts = state.get_accounts()
    assert len(accounts) == 1
    assert accounts[0].id == "acc_yt_1"

    videos = state.get_videos()
    assert len(videos) == 1
    assert videos[0].kind == VideoKind.SHORT

    templates = state.get_templates()
    assert len(templates) == 1
    assert templates[0].id == "tpl_test_1"

    renders = state.get_renders()
    assert len(renders) == 1
    assert renders[0].id == "rnd_test_1"

    batches = state.get_batches()
    assert len(batches) == 1
    assert batches[0].id == "batch_test_1"

    posts = state.get_posts()
    assert len(posts) == 1
    assert posts[0].id == "post_test_1"


def test_ui_state_worker_control(env):
    _, _, state = env

    with patch("clipforge.ui.state.WorkerRunner") as mock_runner_cls:
        runner_instance = MagicMock()
        runner_instance._running = True
        mock_runner_cls.return_value = runner_instance

        # Start workers
        state.start_workers()
        assert state.is_workers_running() is True
        mock_runner_cls.assert_called_once()

        # Stop workers
        runner_instance._running = False
        state.stop_workers()
        assert state.is_workers_running() is False
        runner_instance.stop.assert_called_once()


def test_stat_card_component():
    card = StatCard("Test Metric", 42, "video_library", subtitle="details")
    assert card.value_text.value == "42"
    assert card.subtitle_text.value == "details"

    card.update_value(100, "updated details")
    assert card.value_text.value == "100"
    assert card.subtitle_text.value == "updated details"


def test_app_header_component():
    toggle_mock = MagicMock()
    refresh_mock = MagicMock()

    header = AppHeader(
        title="Test ClipForge",
        on_refresh=refresh_mock,
        on_toggle_workers=toggle_mock,
        is_workers_running=False,
    )

    assert header.title_text.value == "Test ClipForge"
    assert header.worker_status_text.value == "Workers Parados"

    header.set_worker_status(True)
    assert header.worker_status_text.value == "Workers Ativos"
    assert header.toggle_workers_btn.text == "Parar Workers"


def test_views_initialization(env):
    _, _, state = env
    notify_mock = MagicMock()

    # 1. Dashboard View
    dashboard = DashboardView(state, on_notify=notify_mock)
    assert len(dashboard.jobs_table.rows) == 1

    # 2. Accounts View
    accounts = AccountsView(state, on_notify=notify_mock)
    assert accounts.youtube_status_text.value == "Conectado"

    # 3. Downloader View
    downloader = DownloaderView(state, on_notify=notify_mock)
    assert len(downloader.videos_table.rows) == 1

    # 4. Templates View
    templates = TemplatesView(state, on_notify=notify_mock)
    assert len(templates.templates_table.rows) == 1
    assert len(templates.renders_table.rows) == 1

    # 5. Scheduler View
    scheduler = SchedulerView(state, on_notify=notify_mock)
    assert len(scheduler.posts_table.rows) == 1
    assert len(scheduler.video_selection_list.controls) == 1


def test_create_app_main(env):
    _, _, state = env
    main_func = create_app(state=state)
    assert callable(main_func)

    mock_page = MagicMock()
    main_func(mock_page)

    # Verify that controls were added to the page
    mock_page.add.assert_called_once()
    assert mock_page.title.startswith("Clip Forge V2")


def test_cli_ui_help():
    runner = CliRunner()
    result = runner.invoke(cli_main, ["ui", "--help"])
    assert result.exit_code == 0
    assert "Launch the Clip Forge V2 Desktop UI (Flet)" in result.output
    assert "--browser" in result.output


def test_youtube_scraper_flags_compatibility():
    from clipforge.modules.youtube.scraper import YouTubeScraper
    scraper = YouTubeScraper()
    with patch.object(scraper, "_get_ydl_options", return_value={}):
        with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
            mock_ydl = MagicMock()
            mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
            mock_ydl.extract_info.return_value = {
                "entries": [
                    {"id": "test_s1", "title": "Short 1", "duration": 30, "webpage_url": "https://yt.com/watch?v=test_s1"}
                ]
            }

            # Must not raise TypeError: unexpected keyword argument 'only_shorts'
            res = scraper.list_channel_videos("@channel", only_shorts=True)
            assert len(res) == 1
            assert res[0]["id"] == "test_s1"
            assert res[0]["kind"] == VideoKind.SHORT

            # Must not raise TypeError: unexpected keyword argument 'only_lives'
            res_live = scraper.list_channel_videos("@channel", only_lives=True)
            assert len(res_live) == 1
            assert res_live[0]["kind"] == VideoKind.LIVE_VOD


def test_downloader_view_fetch_platform_videos(env):
    _, _, state = env
    downloader = DownloaderView(state)

    # 1. YouTube only shorts
    downloader.platform_dropdown.value = "youtube"
    downloader.only_shorts_cb.value = True
    downloader.only_lives_cb.value = False

    with patch("clipforge.ui.views.downloader_view.YouTubeScraper") as mock_yt_cls:
        mock_yt = MagicMock()
        mock_yt_cls.return_value = mock_yt
        mock_yt.list_channel_videos.return_value = [
            {"id": "s1", "title": "Short 1", "url": "https://yt.com/shorts/s1", "kind": VideoKind.SHORT, "duration_seconds": 25}
        ]

        videos = downloader._fetch_platform_videos("youtube", "@mychannel", 10)
        assert len(videos) == 1
        mock_yt.list_channel_videos.assert_called_once_with(
            channel_url="@mychannel",
            max_results=10,
            tab="shorts",
            only_shorts=True,
        )

    # 2. YouTube only lives
    downloader.only_shorts_cb.value = False
    downloader.only_lives_cb.value = True

    with patch("clipforge.ui.views.downloader_view.YouTubeScraper") as mock_yt_cls:
        mock_yt = MagicMock()
        mock_yt_cls.return_value = mock_yt
        mock_yt.list_channel_videos.return_value = [
            {"id": "l1", "title": "Live 1", "url": "https://yt.com/watch?v=l1", "kind": VideoKind.LIVE_VOD, "duration_seconds": 3600}
        ]

        videos = downloader._fetch_platform_videos("youtube", "@mychannel", 10)
        assert len(videos) == 1
        mock_yt.list_channel_videos.assert_called_once_with(
            channel_url="@mychannel",
            max_results=10,
            tab="streams",
            only_lives=True,
        )

    # 3. YouTube all tabs
    downloader.only_shorts_cb.value = False
    downloader.only_lives_cb.value = False

    with patch("clipforge.ui.views.downloader_view.YouTubeScraper") as mock_yt_cls:
        mock_yt = MagicMock()
        mock_yt_cls.return_value = mock_yt
        mock_yt.list_channel_all_tabs.return_value = [
            {"id": "v1", "title": "Video 1", "url": "https://yt.com/watch?v=v1", "kind": VideoKind.LONG, "duration_seconds": 300}
        ]

        videos = downloader._fetch_platform_videos("youtube", "@mychannel", 10)
        assert len(videos) == 1
        mock_yt.list_channel_all_tabs.assert_called_once_with(
            channel_url="@mychannel",
            max_results_per_tab=10,
            include_lives=True,
        )

    # 4. TikTok
    with patch("clipforge.ui.views.downloader_view.TikTokScraper") as mock_tt_cls:
        mock_tt = MagicMock()
        mock_tt_cls.return_value = mock_tt
        mock_tt_cls.normalize_profile_url.return_value = "https://www.tiktok.com/@creator"
        mock_tt.list_profile_videos.return_value = [
            {"id": "tt1", "title": "TT 1", "url": "https://tiktok.com/@creator/video/1", "kind": VideoKind.SHORT, "duration_seconds": 15}
        ]

        videos = downloader._fetch_platform_videos("tiktok", "@creator", 5)
        assert len(videos) == 1
        mock_tt.list_profile_videos.assert_called_once_with("https://www.tiktok.com/@creator", max_results=5)


def test_downloader_view_enqueue_with_dict_entries(env):
    db, queue, state = env
    notify_mock = MagicMock()
    downloader = DownloaderView(state, on_notify=notify_mock)

    downloader.url_input.value = "https://www.youtube.com/@channel"
    downloader.platform_dropdown.value = "youtube"

    sample_videos = [
        {
            "id": "new_vid_123",
            "title": "Fresh Download",
            "url": "https://www.youtube.com/watch?v=new_vid_123",
            "kind": VideoKind.SHORT,
            "duration_seconds": 45.0,
        }
    ]

    with patch.object(downloader, "_fetch_platform_videos", return_value=sample_videos):
        with patch("threading.Thread") as mock_thread_cls:
            # Execute inline synchronously
            def inline_thread(target, daemon=False):
                target()
                mock_t = MagicMock()
                return mock_t

            mock_thread_cls.side_effect = inline_thread

            downloader._handle_enqueue(None)

    # Check notification
    notify_mock.assert_called_with("1 downloads enfileirados na fila!", False)

    # Verify video was added to DB
    saved = db.get_video_by_source_url("https://www.youtube.com/watch?v=new_vid_123")
    assert saved is not None
    assert saved.title == "Fresh Download"
    assert saved.kind == VideoKind.SHORT

    # Verify job was enqueued
    pending_jobs = queue.list_jobs(status=JobStatus.QUEUED)
    enqueue_job = [j for j in pending_jobs if j.payload.get("video_id") == saved.id]
    assert len(enqueue_job) == 1
    assert enqueue_job[0].type == JobType.DOWNLOAD


