"""Top Header component for Clip Forge V2 UI."""

from __future__ import annotations
from typing import Callable, Optional
import flet as ft


class AppHeader(ft.Container):
    def __init__(
        self,
        title: str = "Clip Forge V2",
        on_refresh: Optional[Callable[[], None]] = None,
        on_toggle_workers: Optional[Callable[[], None]] = None,
        is_workers_running: bool = False,
    ):
        self.title_text = ft.Text(
            title,
            size=20,
            weight=ft.FontWeight.BOLD,
            color=ft.Colors.PRIMARY,
        )

        self.worker_indicator = ft.Container(
            width=10,
            height=10,
            border_radius=5,
            bgcolor=ft.Colors.GREEN if is_workers_running else ft.Colors.OUTLINE,
        )

        self.worker_status_text = ft.Text(
            "Workers Ativos" if is_workers_running else "Workers Parados",
            size=12,
            weight=ft.FontWeight.W_500,
            color=ft.Colors.GREEN if is_workers_running else ft.Colors.OUTLINE,
        )

        self.toggle_workers_btn = ft.FilledButton(
            "Parar Workers" if is_workers_running else "Iniciar Workers",
            icon=ft.Icons.STOP if is_workers_running else ft.Icons.PLAY_ARROW,
            on_click=lambda _: on_toggle_workers() if on_toggle_workers else None,
            style=ft.ButtonStyle(
                color=ft.Colors.WHITE,
                bgcolor=ft.Colors.RED_800 if is_workers_running else ft.Colors.PRIMARY,
            ),
        )

        self.refresh_btn = ft.IconButton(
            icon=ft.Icons.REFRESH,
            tooltip="Atualizar dados",
            on_click=lambda _: on_refresh() if on_refresh else None,
        )

        super().__init__(
            content=ft.Row(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.AUTO_AWESOME, color=ft.Colors.PRIMARY, size=28),
                            self.title_text,
                            ft.Container(
                                content=ft.Text("V2", size=10, weight=ft.FontWeight.BOLD),
                                padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                                border_radius=4,
                                bgcolor=ft.Colors.PRIMARY_CONTAINER,
                            ),
                        ],
                        spacing=8,
                    ),
                    ft.Row(
                        controls=[
                            ft.Row(
                                controls=[
                                    self.worker_indicator,
                                    self.worker_status_text,
                                ],
                                spacing=6,
                            ),
                            ft.Container(width=8),
                            self.toggle_workers_btn,
                            self.refresh_btn,
                        ],
                        spacing=8,
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            padding=ft.Padding.symmetric(horizontal=24, vertical=14),
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
            border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
        )

    def set_worker_status(self, is_running: bool) -> None:
        self.worker_indicator.bgcolor = ft.Colors.GREEN if is_running else ft.Colors.OUTLINE
        self.worker_status_text.value = "Workers Ativos" if is_running else "Workers Parados"
        self.worker_status_text.color = ft.Colors.GREEN if is_running else ft.Colors.OUTLINE
        btn_label = "Parar Workers" if is_running else "Iniciar Workers"
        self.toggle_workers_btn.content = btn_label
        try:
            self.toggle_workers_btn.text = btn_label
        except Exception:
            pass
        self.toggle_workers_btn.icon = ft.Icons.STOP if is_running else ft.Icons.PLAY_ARROW
        self.toggle_workers_btn.style.bgcolor = ft.Colors.RED_800 if is_running else ft.Colors.PRIMARY

