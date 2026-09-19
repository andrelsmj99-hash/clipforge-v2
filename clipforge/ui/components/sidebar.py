"""Sidebar NavigationRail component for Clip Forge V2 UI."""

from __future__ import annotations
from typing import Callable
import flet as ft


class AppSidebar(ft.NavigationRail):
    def __init__(self, on_destination_change: Callable[[int], None]):
        super().__init__(
            selected_index=0,
            label_type=ft.NavigationRailLabelType.ALL,
            min_width=80,
            min_extended_width=180,
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
            destinations=[
                ft.NavigationRailDestination(
                    icon=ft.Icons.DASHBOARD_OUTLINED,
                    selected_icon=ft.Icons.DASHBOARD,
                    label="Dashboard",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.ACCOUNT_CIRCLE_OUTLINED,
                    selected_icon=ft.Icons.ACCOUNT_CIRCLE,
                    label="Contas",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.DOWNLOAD_OUTLINED,
                    selected_icon=ft.Icons.DOWNLOAD,
                    label="Downloads",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.MOVIE_CREATION_OUTLINED,
                    selected_icon=ft.Icons.MOVIE_CREATION,
                    label="Canva / Render",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.SCHEDULE_OUTLINED,
                    selected_icon=ft.Icons.SCHEDULE,
                    label="Agendador",
                ),
            ],
            on_change=lambda e: on_destination_change(e.control.selected_index),
        )
