"""Downloader view: Scrape channels/profiles and manage downloaded video library."""

from __future__ import annotations
import threading
from typing import Callable, Optional
import flet as ft

from clipforge.core.models import JobType, Platform, VideoKind, VideoStatus
from clipforge.modules.instagram.scraper import InstagramScraper
from clipforge.modules.tiktok.scraper import TikTokScraper
from clipforge.modules.youtube.scraper import YouTubeScraper
from clipforge.ui.state import UIState


class DownloaderView(ft.Container):
    def __init__(self, state: UIState, on_notify: Optional[Callable[[str, bool], None]] = None):
        self.state = state
        self.on_notify = on_notify

        # Inputs
        self.platform_dropdown = ft.Dropdown(
            label="Plataforma",
            value="youtube",
            options=[
                ft.dropdown.Option("youtube", "YouTube"),
                ft.dropdown.Option("tiktok", "TikTok"),
                ft.dropdown.Option("instagram", "Instagram"),
            ],
            width=180,
        )

        self.url_input = ft.TextField(
            label="URL do Canal ou Perfil (ex: @mrbeast)",
            hint_text="https://www.youtube.com/@Canal ou @usuario",
            expand=True,
        )

        self.only_shorts_cb = ft.Checkbox(label="Apenas Shorts", value=False)
        self.only_lives_cb = ft.Checkbox(label="Apenas Lives (VODs)", value=False)
        self.max_results_input = ft.TextField(label="Limite", value="10", width=90)

        self.btn_scrape = ft.OutlinedButton("Listar Vídeos", icon=ft.Icons.SEARCH, on_click=self._handle_scrape)
        self.btn_enqueue = ft.FilledButton("Enfileirar Downloads", icon=ft.Icons.DOWNLOAD, on_click=self._handle_enqueue)
        self.loading_indicator = ft.ProgressRing(width=20, height=20, visible=False)

        # Scraped list output
        self.scraped_info_text = ft.Text("", size=12, color=ft.Colors.OUTLINE)

        # Videos Table
        self.videos_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("ID")),
                ft.DataColumn(ft.Text("Título")),
                ft.DataColumn(ft.Text("Tipo")),
                ft.DataColumn(ft.Text("Duração")),
                ft.DataColumn(ft.Text("Status")),
                ft.DataColumn(ft.Text("Baixado em")),
            ],
            rows=[],
            heading_row_color=ft.Colors.SURFACE_CONTAINER_HIGHEST,
        )

        self.videos_container = ft.Container(
            content=ft.Column(
                controls=[self.videos_table],
                scroll=ft.ScrollMode.AUTO,
            ),
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=8,
            padding=8,
            height=340,
        )

        super().__init__(
            content=ft.Column(
                controls=[
                    ft.Text("Downloader Multi-Plataforma", size=24, weight=ft.FontWeight.BOLD),
                    ft.Text("Extraia vídeos públicos do YouTube, TikTok e Instagram diretamente para o banco local.", size=13, color=ft.Colors.OUTLINE),
                    ft.Container(height=12),
                    # Input Form
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Row(controls=[self.platform_dropdown, self.url_input], spacing=12),
                                ft.Row(
                                    controls=[
                                        self.only_shorts_cb,
                                        self.only_lives_cb,
                                        self.max_results_input,
                                        ft.Container(width=12),
                                        self.btn_scrape,
                                        self.btn_enqueue,
                                        self.loading_indicator,
                                    ],
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    spacing=12,
                                ),
                                self.scraped_info_text,
                            ]
                        ),
                        padding=16,
                        border_radius=10,
                        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                    ),
                    ft.Container(height=16),
                    ft.Text("Biblioteca de Vídeos Locais", size=18, weight=ft.FontWeight.BOLD),
                    ft.Container(height=8),
                    self.videos_container,
                ],
                scroll=ft.ScrollMode.AUTO,
                spacing=8,
            ),
            padding=24,
            expand=True,
        )

        self.refresh()

    def refresh(self) -> None:
        videos = self.state.get_videos(limit=50)
        new_rows = []
        for v in videos:
            dur_min = int(v.duration_seconds // 60) if v.duration_seconds else 0
            dur_sec = int(v.duration_seconds % 60) if v.duration_seconds else 0
            dur_str = f"{dur_min}:{dur_sec:02d}" if v.duration_seconds else "-"

            status_color = ft.Colors.GREEN if v.status == VideoStatus.DOWNLOADED else (ft.Colors.RED if v.status == VideoStatus.ERROR else ft.Colors.AMBER)
            kind_color = ft.Colors.PURPLE if v.kind == VideoKind.SHORT else (ft.Colors.CYAN if v.kind == VideoKind.LIVE_VOD else ft.Colors.BLUE)

            downloaded_str = v.downloaded_at.strftime("%d/%m %H:%M") if v.downloaded_at else "-"

            new_rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(v.id, size=11, weight=ft.FontWeight.W_500)),
                        ft.DataCell(ft.Text((v.title or v.source_url)[:45], size=12)),
                        ft.DataCell(ft.Container(
                            content=ft.Text(v.kind.value.upper(), size=10, color=kind_color, weight=ft.FontWeight.BOLD),
                            padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                            border_radius=4,
                            border=ft.Border.all(1, kind_color),
                        )),
                        ft.DataCell(ft.Text(dur_str, size=12)),
                        ft.DataCell(ft.Container(
                            content=ft.Text(v.status.value.upper(), size=10, color=status_color, weight=ft.FontWeight.BOLD),
                            padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                            border_radius=4,
                            bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
                        )),
                        ft.DataCell(ft.Text(downloaded_str, size=11)),
                    ]
                )
            )
        self.videos_table.rows = new_rows

    def _fetch_platform_videos(self, platform: str, channel_url: str, max_results: int) -> list:
        if platform == "youtube":
            scraper = YouTubeScraper()
            if self.only_shorts_cb.value:
                videos = scraper.list_channel_videos(
                    channel_url=channel_url,
                    max_results=max_results,
                    tab="shorts",
                    only_shorts=True,
                )
            elif self.only_lives_cb.value:
                videos = scraper.list_channel_videos(
                    channel_url=channel_url,
                    max_results=max_results,
                    tab="streams",
                    only_lives=True,
                )
            else:
                videos = scraper.list_channel_all_tabs(
                    channel_url=channel_url,
                    max_results_per_tab=max_results,
                    include_lives=True,
                )
            if max_results and len(videos) > max_results:
                videos = videos[:max_results]
            return videos
        elif platform == "tiktok":
            scraper = TikTokScraper()
            profile_url = TikTokScraper.normalize_profile_url(channel_url)
            return scraper.list_profile_videos(profile_url, max_results=max_results)
        else:
            scraper = InstagramScraper()
            profile_url = InstagramScraper.normalize_profile_url(channel_url)
            return scraper.list_profile_videos(profile_url, max_results=max_results)

    def _handle_scrape(self, _):
        channel_url = self.url_input.value.strip()
        if not channel_url:
            if self.on_notify:
                self.on_notify("Informe a URL do canal ou perfil.", True)
            return

        platform = self.platform_dropdown.value
        max_results = int(self.max_results_input.value or "10")

        self.loading_indicator.visible = True
        self.btn_scrape.disabled = True
        self.scraped_info_text.value = "Consultando perfil..."

        def _task():
            try:
                videos = self._fetch_platform_videos(platform, channel_url, max_results)
                self.scraped_info_text.value = f"Encontrados {len(videos)} vídeos disponíveis para download."
                if self.on_notify:
                    self.on_notify(f"{len(videos)} vídeos listados!", False)
            except Exception as e:
                self.scraped_info_text.value = f"Erro na consulta: {e}"
                if self.on_notify:
                    self.on_notify(f"Falha ao listar: {e}", True)
            finally:
                self.loading_indicator.visible = False
                self.btn_scrape.disabled = False
                self.refresh()

        threading.Thread(target=_task, daemon=True).start()

    def _handle_enqueue(self, _):
        channel_url = self.url_input.value.strip()
        if not channel_url:
            if self.on_notify:
                self.on_notify("Informe a URL do canal ou perfil para enfileirar.", True)
            return

        platform = self.platform_dropdown.value
        max_results = int(self.max_results_input.value or "10")

        self.loading_indicator.visible = True
        self.btn_enqueue.disabled = True

        def _task():
            try:
                videos = self._fetch_platform_videos(platform, channel_url, max_results)
                enqueued_count = 0
                for v in videos:
                    v_url = v["url"] if isinstance(v, dict) else getattr(v, "url", None)
                    v_id = v["id"] if isinstance(v, dict) else getattr(v, "id", None)
                    if not v_url or not v_id:
                        continue

                    v_title = v.get("title") if isinstance(v, dict) else getattr(v, "title", f"Video {v_id}")
                    v_kind = v.get("kind") if isinstance(v, dict) else getattr(v, "kind", VideoKind.UNKNOWN)
                    v_duration = v.get("duration_seconds") if isinstance(v, dict) else getattr(v, "duration_seconds", None)

                    # Deduplication check
                    existing = self.state.db.get_video_by_source_url(v_url)
                    if existing and existing.status == VideoStatus.DOWNLOADED:
                        continue

                    vid_id = existing.id if existing else f"vid_{platform}_{v_id}"
                    if not existing:
                        from clipforge.core.models import Video
                        self.state.db.save_video(
                            Video(
                                id=vid_id,
                                source_url=v_url,
                                title=v_title,
                                kind=v_kind or VideoKind.UNKNOWN,
                                duration_seconds=v_duration,
                                status=VideoStatus.PENDING,
                            )
                        )
                    # Enqueue download job
                    self.state.queue.enqueue(
                        job_type=JobType.DOWNLOAD,
                        payload={
                            "video_id": vid_id,
                            "source_url": v_url,
                            "platform": platform,
                        },
                    )
                    enqueued_count += 1

                if self.on_notify:
                    self.on_notify(f"{enqueued_count} downloads enfileirados na fila!", False)
            except Exception as e:
                if self.on_notify:
                    self.on_notify(f"Erro ao enfileirar downloads: {e}", True)
            finally:
                self.loading_indicator.visible = False
                self.btn_enqueue.disabled = False
                self.refresh()

        threading.Thread(target=_task, daemon=True).start()

