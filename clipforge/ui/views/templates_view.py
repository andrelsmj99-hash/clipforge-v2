"""Templates and Render Studio view: Canva templates and automated rendering."""

from __future__ import annotations
import threading
from typing import Callable, Optional
import flet as ft

from clipforge.core.models import JobType, RenderStatus
from clipforge.modules.templates.manager import TemplateManager
from clipforge.modules.templates.mapper import TemplateMapper
from clipforge.ui.state import UIState


class TemplatesView(ft.Container):
    def __init__(self, state: UIState, on_notify: Optional[Callable[[str, bool], None]] = None):
        self.state = state
        self.on_notify = on_notify

        # Template Registration Inputs
        self.tpl_name_input = ft.TextField(label="Nome do Template", hint_text="Ex: Shorts Dark 01", width=220)
        self.tpl_url_input = ft.TextField(
            label="URL de Edição do Canva",
            hint_text="https://www.canva.com/design/DAGXXXXX/edit",
            expand=True,
        )
        self.btn_add_tpl = ft.FilledButton("Adicionar Template", icon=ft.Icons.ADD, on_click=self._handle_add_template)

        # Templates Table
        self.templates_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("ID")),
                ft.DataColumn(ft.Text("Nome")),
                ft.DataColumn(ft.Text("Canva Design ID")),
                ft.DataColumn(ft.Text("Status")),
                ft.DataColumn(ft.Text("Ações")),
            ],
            rows=[],
            heading_row_color=ft.Colors.SURFACE_CONTAINER_HIGHEST,
        )

        # Render Studio Controls
        self.render_video_dropdown = ft.Dropdown(label="Vídeo Fonte (Baixado)", expand=True)
        self.render_template_dropdown = ft.Dropdown(label="Template Canva (Mapeado)", expand=True)
        self.btn_enqueue_render = ft.FilledButton("Enfileirar Render", icon=ft.Icons.MOVIE, on_click=self._handle_enqueue_render)

        # Renders Table
        self.renders_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("ID")),
                ft.DataColumn(ft.Text("Vídeo ID")),
                ft.DataColumn(ft.Text("Template ID")),
                ft.DataColumn(ft.Text("Status")),
                ft.DataColumn(ft.Text("Arquivo Final")),
            ],
            rows=[],
            heading_row_color=ft.Colors.SURFACE_CONTAINER_HIGHEST,
        )

        super().__init__(
            content=ft.Column(
                controls=[
                    ft.Text("Canva Templates & Render Studio", size=24, weight=ft.FontWeight.BOLD),
                    ft.Text("Gerencie os templates cadastrados, mapeie placeholders em 1-clique e renderize vídeos.", size=13, color=ft.Colors.OUTLINE),
                    ft.Container(height=12),
                    # Section 1: Template Registration
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Text("Cadastrar Novo Template", size=16, weight=ft.FontWeight.BOLD),
                                ft.Row(controls=[self.tpl_name_input, self.tpl_url_input, self.btn_add_tpl], spacing=12),
                            ]
                        ),
                        padding=16,
                        border_radius=10,
                        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                    ),
                    ft.Container(height=12),
                    # Section 2: Registered Templates
                    ft.Text("Templates Cadastrados", size=18, weight=ft.FontWeight.BOLD),
                    ft.Container(
                        content=ft.Column(controls=[self.templates_table], scroll=ft.ScrollMode.AUTO),
                        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                        border_radius=8,
                        padding=8,
                        height=200,
                    ),
                    ft.Container(height=16),
                    # Section 3: Render Studio
                    ft.Text("Render Studio", size=18, weight=ft.FontWeight.BOLD),
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Row(controls=[self.render_video_dropdown, self.render_template_dropdown], spacing=12),
                                ft.Row(controls=[self.btn_enqueue_render], alignment=ft.MainAxisAlignment.END),
                            ]
                        ),
                        padding=16,
                        border_radius=10,
                        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                    ),
                    ft.Container(height=12),
                    # Section 4: Rendered Outputs
                    ft.Text("Histórico de Renderizações", size=18, weight=ft.FontWeight.BOLD),
                    ft.Container(
                        content=ft.Column(controls=[self.renders_table], scroll=ft.ScrollMode.AUTO),
                        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                        border_radius=8,
                        padding=8,
                        height=200,
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
        templates = self.state.get_templates()
        new_tpl_rows = []
        tpl_options = []

        for t in templates:
            is_mapped = bool(t.placeholder_map)
            status_color = ft.Colors.GREEN if is_mapped else ft.Colors.AMBER
            status_text = "MAPEADO" if is_mapped else "NÃO MAPEADO"

            if is_mapped:
                tpl_options.append(ft.dropdown.Option(t.id, f"{t.name} ({t.id})"))

            new_tpl_rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(t.id, size=11, weight=ft.FontWeight.W_500)),
                        ft.DataCell(ft.Text(t.name, size=12, weight=ft.FontWeight.BOLD)),
                        ft.DataCell(ft.Text(t.canva_design_id or "-", size=11)),
                        ft.DataCell(ft.Container(
                            content=ft.Text(status_text, size=10, color=status_color, weight=ft.FontWeight.BOLD),
                            padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                            border_radius=4,
                            border=ft.Border.all(1, status_color),
                        )),
                        ft.DataCell(
                            ft.Row(
                                controls=[
                                    ft.OutlinedButton(
                                        "Mapear (1-Clique)",
                                        icon=ft.Icons.TOUCH_APP,
                                        on_click=lambda _, tid=t.id: self._handle_map_template(tid),
                                    ),
                                    ft.IconButton(
                                        icon=ft.Icons.DELETE_OUTLINE,
                                        icon_color=ft.Colors.RED,
                                        tooltip="Excluir",
                                        on_click=lambda _, tid=t.id: self._handle_delete_template(tid),
                                    ),
                                ],
                                spacing=4,
                            )
                        ),
                    ]
                )
            )

        self.templates_table.rows = new_tpl_rows
        self.render_template_dropdown.options = tpl_options
        if tpl_options and not self.render_template_dropdown.value:
            self.render_template_dropdown.value = tpl_options[0].key

        # Populate Video Dropdown for rendering
        videos = self.state.get_videos(limit=50)
        vid_options = [
            ft.dropdown.Option(v.id, f"{v.title or v.id} ({v.kind.value})")
            for v in videos
        ]
        self.render_video_dropdown.options = vid_options
        if vid_options and not self.render_video_dropdown.value:
            self.render_video_dropdown.value = vid_options[0].key

        # Populate Renders
        renders = self.state.get_renders(limit=30)
        new_render_rows = []
        for r in renders:
            status_color = ft.Colors.GREEN if r.status == RenderStatus.COMPLETED else (ft.Colors.RED if r.status == RenderStatus.FAILED else ft.Colors.AMBER)
            new_render_rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(r.id, size=11, weight=ft.FontWeight.W_500)),
                        ft.DataCell(ft.Text(r.video_id, size=11)),
                        ft.DataCell(ft.Text(r.template_id or "-", size=11)),
                        ft.DataCell(ft.Container(
                            content=ft.Text(r.status.value.upper(), size=10, color=status_color, weight=ft.FontWeight.BOLD),
                            padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                            border_radius=4,
                            border=ft.Border.all(1, status_color),
                        )),
                        ft.DataCell(ft.Text(r.output_path or "-", size=11, color=ft.Colors.OUTLINE)),
                    ]
                )
            )
        self.renders_table.rows = new_render_rows

    def _handle_add_template(self, _):
        name = self.tpl_name_input.value.strip()
        url = self.tpl_url_input.value.strip()
        if not name or not url:
            if self.on_notify:
                self.on_notify("Informe o nome e a URL do Canva.", True)
            return

        try:
            mgr = TemplateManager(self.state.db)
            mgr.add_template(name=name, template_url=url)
            self.tpl_name_input.value = ""
            self.tpl_url_input.value = ""
            if self.on_notify:
                self.on_notify(f"Template '{name}' cadastrado com sucesso!", False)
            self.refresh()
        except Exception as e:
            if self.on_notify:
                self.on_notify(f"Erro ao salvar template: {e}", True)

    def _handle_delete_template(self, template_id: str):
        try:
            self.state.db.delete_template(template_id)
            if self.on_notify:
                self.on_notify("Template removido.", False)
            self.refresh()
        except Exception as e:
            if self.on_notify:
                self.on_notify(f"Erro ao excluir template: {e}", True)

    def _handle_map_template(self, template_id: str):
        if self.on_notify:
            self.on_notify("Iniciando mapeador interativo do Canva... Veja a janela do navegador.", False)

        def _task():
            try:
                mapper = TemplateMapper(self.state.db)
                mapper.map_template(template_id)
                if self.on_notify:
                    self.on_notify("Placeholder mapeado com sucesso no template!", False)
            except Exception as e:
                if self.on_notify:
                    self.on_notify(f"Erro no mapeamento do template: {e}", True)
            self.refresh()

        threading.Thread(target=_task, daemon=True).start()

    def _handle_enqueue_render(self, _):
        video_id = self.render_video_dropdown.value
        template_id = self.render_template_dropdown.value
        if not video_id or not template_id:
            if self.on_notify:
                self.on_notify("Selecione um vídeo e um template mapeado.", True)
            return

        try:
            job = self.state.queue.enqueue(
                job_type=JobType.RENDER,
                payload={"video_id": video_id, "template_id": template_id},
            )
            if self.on_notify:
                self.on_notify(f"Job de renderização {job.id} enfileirado com sucesso!", False)
            self.refresh()
        except Exception as e:
            if self.on_notify:
                self.on_notify(f"Erro ao enfileirar render: {e}", True)
