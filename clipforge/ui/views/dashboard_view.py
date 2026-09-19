"""Dashboard view: System metrics, queue monitor, and recent jobs."""

from __future__ import annotations
from typing import Callable, Optional
import flet as ft

from clipforge.core.models import JobStatus, JobType
from clipforge.ui.components.stat_card import StatCard
from clipforge.ui.state import UIState


class DashboardView(ft.Container):
    def __init__(self, state: UIState, on_notify: Optional[Callable[[str, bool], None]] = None):
        self.state = state
        self.on_notify = on_notify

        # Metric Cards
        self.card_videos = StatCard("Vídeos Baixados", "0", ft.Icons.VIDEO_LIBRARY, ft.Colors.BLUE, "0 shorts")
        self.card_jobs = StatCard("Fila de Jobs", "0", ft.Icons.QUEUE, ft.Colors.AMBER, "0 pendentes")
        self.card_templates = StatCard("Templates Canva", "0", ft.Icons.DESIGN_SERVICES, ft.Colors.PURPLE, "0 mapeados")
        self.card_renders = StatCard("Renders Prontos", "0", ft.Icons.MOVIE, ft.Colors.CYAN, "Prontos para post")
        self.card_posts = StatCard("Posts Agendados", "0", ft.Icons.SCHEDULE_SEND, ft.Colors.GREEN, "0 lotes")

        # Jobs Table
        self.jobs_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("ID")),
                ft.DataColumn(ft.Text("Tipo")),
                ft.DataColumn(ft.Text("Status")),
                ft.DataColumn(ft.Text("Tentativas")),
                ft.DataColumn(ft.Text("Criado em")),
                ft.DataColumn(ft.Text("Erro / Detalhes")),
            ],
            rows=[],
            heading_row_color=ft.Colors.SURFACE_CONTAINER_HIGHEST,
        )

        self.jobs_container = ft.Container(
            content=ft.Column(
                controls=[self.jobs_table],
                scroll=ft.ScrollMode.AUTO,
            ),
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=8,
            padding=8,
            height=380,
        )

        super().__init__(
            content=ft.Column(
                controls=[
                    ft.Text("Painel Geral", size=24, weight=ft.FontWeight.BOLD),
                    ft.Text("Visão em tempo real da fila de processamento, mídia e agendamentos.", size=13, color=ft.Colors.OUTLINE),
                    ft.Container(height=12),
                    # Cards Row
                    ft.Row(
                        controls=[
                            self.card_videos,
                            self.card_jobs,
                            self.card_templates,
                            self.card_renders,
                            self.card_posts,
                        ],
                        wrap=True,
                        spacing=16,
                    ),
                    ft.Container(height=20),
                    # Section Header
                    ft.Row(
                        controls=[
                            ft.Text("Jobs Recentes na Fila", size=18, weight=ft.FontWeight.BOLD),
                            ft.FilledTonalButton(
                                "Limpar Travas Antigas",
                                icon=ft.Icons.CLEANING_SERVICES,
                                on_click=self._handle_clear_locks,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Container(height=8),
                    self.jobs_container,
                ],
                scroll=ft.ScrollMode.AUTO,
                spacing=8,
            ),
            padding=24,
            expand=True,
        )

        self.refresh()

    def refresh(self) -> None:
        stats = self.state.get_stats()

        self.card_videos.update_value(stats["total_videos"], f"{stats['total_shorts']} shorts")
        self.card_jobs.update_value(stats["total_jobs"], f"{stats['queued_jobs']} fila | {stats['running_jobs']} rodando")
        self.card_templates.update_value(stats["total_templates"], f"{stats['mapped_templates']} mapeados")
        self.card_renders.update_value(stats["total_renders"], f"Vídeos editados")
        self.card_posts.update_value(stats["scheduled_posts"], f"{stats['total_batches']} lotes criados")

        jobs = self.state.get_recent_jobs(limit=25)
        new_rows = []
        for j in jobs:
            status_color = ft.Colors.BLUE
            if j.status == JobStatus.RUNNING:
                status_color = ft.Colors.AMBER
            elif j.status == JobStatus.COMPLETED:
                status_color = ft.Colors.GREEN
            elif j.status == JobStatus.FAILED:
                status_color = ft.Colors.RED

            type_name = j.type.value if hasattr(j.type, "value") else str(j.type)
            created_str = j.created_at.strftime("%d/%m %H:%M:%S") if j.created_at else "-"
            err_text = (j.error or "-")[:50]

            new_rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(j.id, size=12, weight=ft.FontWeight.W_500)),
                        ft.DataCell(ft.Container(
                            content=ft.Text(type_name.upper(), size=11, weight=ft.FontWeight.BOLD),
                            padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                            border_radius=4,
                            bgcolor=ft.Colors.SECONDARY_CONTAINER,
                        )),
                        ft.DataCell(ft.Container(
                            content=ft.Text(j.status.value.upper(), size=11, color=status_color, weight=ft.FontWeight.BOLD),
                            padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                            border_radius=4,
                            border=ft.Border.all(1, status_color),
                        )),
                        ft.DataCell(ft.Text(f"{j.attempts}/{j.max_attempts}", size=12)),
                        ft.DataCell(ft.Text(created_str, size=12)),
                        ft.DataCell(ft.Text(err_text, size=11, color=ft.Colors.RED if j.error else ft.Colors.OUTLINE)),
                    ]
                )
            )

        self.jobs_table.rows = new_rows

    def _handle_clear_locks(self, _):
        count = self.state.queue.clear_stale_locks(timeout_seconds=600)
        if self.on_notify:
            self.on_notify(f"Travas liberadas: {count} jobs recuperados.", False)
        self.refresh()
