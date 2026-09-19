"""Scheduler view: Batch creation, dynamic caption templating, and native schedule publishing."""

from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional
import flet as ft

from clipforge.core.models import Platform, PostStatus, RenderStatus
from clipforge.modules.agendador.due_scanner import DueScanner
from clipforge.modules.agendador.scheduler import BatchScheduler
from clipforge.ui.state import UIState


class SchedulerView(ft.Container):
    def __init__(self, state: UIState, on_notify: Optional[Callable[[str, bool], None]] = None):
        self.state = state
        self.on_notify = on_notify

        # Inputs
        self.platform_dropdown = ft.Dropdown(
            label="Plataforma de Destino",
            value="youtube",
            options=[
                ft.dropdown.Option("youtube", "YouTube"),
                ft.dropdown.Option("tiktok", "TikTok"),
                ft.dropdown.Option("instagram", "Instagram"),
            ],
            width=200,
            on_select=self._handle_platform_change,
        )

        self.account_dropdown = ft.Dropdown(
            label="Conta Conectada",
            expand=True,
        )

        self.caption_input = ft.TextField(
            label="Template de Legenda (aceita {n} e {total})",
            value="Parte {n}/{total} 🔥 Não perca!",
            hint_text="Ex: Episódio {n} de {total} #shorts #viral",
            expand=True,
            on_change=self._handle_caption_change,
        )

        self.caption_preview_text = ft.Text(
            "Preview (Post 1 de 3): Parte 1/3 🔥 Não perca!",
            size=12,
            color=ft.Colors.PRIMARY,
            weight=ft.FontWeight.W_500,
        )

        self.interval_input = ft.TextField(
            label="Intervalo (min)",
            value="60",
            width=120,
        )

        self.native_schedule_cb = ft.Checkbox(
            label="Agendamento Nativo Imediato (envia direto para a plataforma)",
            value=True,
        )

        self.source_type = ft.RadioGroup(
            content=ft.Row(
                controls=[
                    ft.Radio(value="videos", label="Vídeos Baixados"),
                    ft.Radio(value="renders", label="Renders Prontos (Canva/Editor)"),
                ],
                spacing=16,
            ),
            value="videos",
            on_change=lambda _: self._update_items_list(),
        )

        self.video_selection_list = ft.ListView(
            spacing=4,
            height=160,
            padding=8,
        )

        self.btn_create_batch = ft.FilledButton(
            "Criar Lote de Agendamento",
            icon=ft.Icons.SCHEDULE_SEND,
            on_click=self._handle_create_batch,
        )

        self.btn_scan_due = ft.OutlinedButton(
            "Escanear Vencidos (Due Scanner)",
            icon=ft.Icons.SYNC,
            on_click=self._handle_scan_due,
        )

        # Scheduled Posts Table
        self.posts_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("Post ID")),
                ft.DataColumn(ft.Text("Plataforma")),
                ft.DataColumn(ft.Text("Agendado Para")),
                ft.DataColumn(ft.Text("Status")),
                ft.DataColumn(ft.Text("Legenda")),
            ],
            rows=[],
            heading_row_color=ft.Colors.SURFACE_CONTAINER_HIGHEST,
        )

        super().__init__(
            content=ft.Column(
                controls=[
                    ft.Text("Agendador em Massa & Lotes", size=24, weight=ft.FontWeight.BOLD),
                    ft.Text("Agrupe múltiplos vídeos com legendas dinâmicas e agendamento nativo por plataforma.", size=13, color=ft.Colors.OUTLINE),
                    ft.Container(height=12),
                    # Section 1: Batch Configuration
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Text("Configurar Lote de Postagens", size=16, weight=ft.FontWeight.BOLD),
                                ft.Row(controls=[self.platform_dropdown, self.account_dropdown], spacing=12),
                                ft.Row(controls=[self.caption_input, self.interval_input], spacing=12),
                                self.caption_preview_text,
                                ft.Row(
                                    controls=[
                                        self.native_schedule_cb,
                                    ],
                                ),
                                ft.Container(height=4),
                                ft.Text("Origem dos vídeos:", size=13, weight=ft.FontWeight.BOLD),
                                self.source_type,
                                ft.Container(
                                    content=self.video_selection_list,
                                    border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                                    border_radius=8,
                                    bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
                                ),
                                ft.Container(height=4),
                                ft.Row(
                                    controls=[self.btn_scan_due, self.btn_create_batch],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                ),
                            ],
                            spacing=10,
                        ),
                        padding=18,
                        border_radius=10,
                        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                    ),
                    ft.Container(height=16),
                    # Section 2: Scheduled Posts List
                    ft.Text("Posts Agendados", size=18, weight=ft.FontWeight.BOLD),
                    ft.Container(
                        content=ft.Column(controls=[self.posts_table], scroll=ft.ScrollMode.AUTO),
                        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                        border_radius=8,
                        padding=8,
                        height=280,
                    ),
                ],
                scroll=ft.ScrollMode.AUTO,
                spacing=8,
            ),
            padding=24,
            expand=True,
        )

        self.refresh()

    def refresh(self) -> None:
        self._update_accounts_dropdown()
        self._update_items_list()

        posts = self.state.get_posts(limit=30)
        new_rows = []
        for p in posts:
            status_color = ft.Colors.GREEN if p.status == PostStatus.PUBLISHED else (ft.Colors.AMBER if p.status == PostStatus.SCHEDULED else (ft.Colors.RED if p.status == PostStatus.FAILED else ft.Colors.BLUE))
            sched_str = p.scheduled_at.strftime("%d/%m/%Y %H:%M") if p.scheduled_at else "-"

            new_rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(p.id, size=11, weight=ft.FontWeight.W_500)),
                        ft.DataCell(ft.Text(p.platform.value.upper(), size=11, weight=ft.FontWeight.BOLD)),
                        ft.DataCell(ft.Text(sched_str, size=11)),
                        ft.DataCell(ft.Container(
                            content=ft.Text(p.status.value.upper(), size=10, color=status_color, weight=ft.FontWeight.BOLD),
                            padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                            border_radius=4,
                            border=ft.Border.all(1, status_color),
                        )),
                        ft.DataCell(ft.Text((p.caption or "-")[:40], size=11)),
                    ]
                )
            )
        self.posts_table.rows = new_rows

    def _update_accounts_dropdown(self) -> None:
        platform_str = self.platform_dropdown.value or "youtube"
        platform_enum = Platform(platform_str)
        accounts = [a for a in self.state.get_accounts() if a.platform == platform_enum]

        options = [ft.dropdown.Option(a.id, f"{a.name or a.id} ({a.status.value})") for a in accounts]
        self.account_dropdown.options = options
        if options and not self.account_dropdown.value:
            self.account_dropdown.value = options[0].key

    def _update_items_list(self) -> None:
        checkboxes = []
        if self.source_type.value == "renders":
            renders = self.state.get_renders(limit=50)
            completed_renders = [r for r in renders if r.status == RenderStatus.COMPLETED and r.output_path]
            for r in completed_renders:
                checkboxes.append(
                    ft.Checkbox(
                        label=f"[{r.id}] Vídeo: {r.video_id} (Template: {r.template_id or 'Custom'})",
                        value=False,
                        data=r.id,
                    )
                )
        else:
            videos = self.state.get_videos(limit=30)
            for v in videos:
                checkboxes.append(
                    ft.Checkbox(
                        label=f"[{v.id}] {v.title or v.source_url} ({v.kind.value})",
                        value=False,
                        data=v.id,
                    )
                )
        self.video_selection_list.controls = checkboxes

    def _handle_platform_change(self, _):
        self.account_dropdown.value = None
        self._update_accounts_dropdown()

    def _handle_caption_change(self, _):
        template = self.caption_input.value or ""
        preview = template.replace("{n}", "1").replace("{total}", "3")
        self.caption_preview_text.value = f"Preview (Post 1 de 3): {preview}"

    def _handle_create_batch(self, _):
        account_id = self.account_dropdown.value
        platform_str = self.platform_dropdown.value
        if not account_id or not platform_str:
            if self.on_notify:
                self.on_notify("Selecione a plataforma e uma conta conectada.", True)
            return

        selected_ids = [
            cb.data for cb in self.video_selection_list.controls
            if isinstance(cb, ft.Checkbox) and cb.value
        ]

        if not selected_ids:
            if self.on_notify:
                self.on_notify("Selecione pelo menos um item para agendar.", True)
            return

        caption_template = self.caption_input.value or "Vídeo {n}/{total}"
        interval_minutes = int(self.interval_input.value or "60")
        interval_seconds = interval_minutes * 60
        start_at = datetime.now(timezone.utc) + timedelta(minutes=5)

        is_render = (self.source_type.value == "renders")

        try:
            scheduler = BatchScheduler(self.state.db, self.state.queue)
            batch = scheduler.create_batch(
                account_id=account_id,
                platform=Platform(platform_str),
                video_ids=None if is_render else selected_ids,
                render_ids=selected_ids if is_render else None,
                caption_template=caption_template,
                interval_seconds=interval_seconds,
                start_at=start_at,
                native_schedule=self.native_schedule_cb.value,
            )
            if self.on_notify:
                self.on_notify(f"Lote '{batch.id}' criado com {len(selected_ids)} postagens!", False)
            self.refresh()
        except Exception as e:
            if self.on_notify:
                self.on_notify(f"Erro ao criar lote: {e}", True)

    def _handle_scan_due(self, _):
        try:
            scanner = DueScanner(self.state.db, self.state.queue)
            enqueued = scanner.run_once()
            if self.on_notify:
                self.on_notify(f"Due Scanner executado: {enqueued} publicações vencidas enfileiradas.", False)
            self.refresh()
        except Exception as e:
            if self.on_notify:
                self.on_notify(f"Erro no Due Scanner: {e}", True)
