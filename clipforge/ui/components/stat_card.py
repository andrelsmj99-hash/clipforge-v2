"""Reusable StatCard component for Clip Forge V2 UI."""

from __future__ import annotations
import flet as ft


class StatCard(ft.Container):
    def __init__(
        self,
        title: str,
        value: str | int,
        icon: str,
        icon_color: str = ft.Colors.PRIMARY,
        subtitle: str = "",
        width: int = 210,
    ):
        self.value_text = ft.Text(
            str(value),
            size=26,
            weight=ft.FontWeight.BOLD,
            color=ft.Colors.ON_SURFACE,
        )
        self.subtitle_text = ft.Text(
            subtitle,
            size=11,
            color=ft.Colors.OUTLINE,
        )

        super().__init__(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(icon, color=icon_color, size=24),
                            ft.Text(
                                title,
                                size=13,
                                weight=ft.FontWeight.W_500,
                                color=ft.Colors.ON_SURFACE_VARIANT,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Container(height=4),
                    self.value_text,
                    self.subtitle_text,
                ],
                spacing=2,
            ),
            padding=16,
            border_radius=12,
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            width=width,
        )

    def update_value(self, new_value: str | int, subtitle: str = "") -> None:
        self.value_text.value = str(new_value)
        if subtitle:
            self.subtitle_text.value = subtitle
