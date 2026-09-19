"""
Main Application window and navigation router for Clip Forge V2 Desktop UI.
"""

from __future__ import annotations
import logging
from typing import Optional
import flet as ft

from clipforge.ui.components.header import AppHeader
from clipforge.ui.components.sidebar import AppSidebar
from clipforge.ui.state import UIState
from clipforge.ui.views.accounts_view import AccountsView
from clipforge.ui.views.dashboard_view import DashboardView
from clipforge.ui.views.downloader_view import DownloaderView
from clipforge.ui.views.scheduler_view import SchedulerView
from clipforge.ui.views.templates_view import TemplatesView

logger = logging.getLogger(__name__)


def create_app(state: Optional[UIState] = None):
    app_state = state or UIState()

    def main(page: ft.Page):
        page.title = "Clip Forge V2 — Video Automation Engine"
        page.theme_mode = ft.ThemeMode.DARK
        page.padding = 0
        page.spacing = 0

        # Configure Window dimensions
        if hasattr(page, "window") and page.window:
            try:
                page.window.width = 1280
                page.window.height = 840
                page.window.min_width = 1000
                page.window.min_height = 680
            except Exception:
                pass

        def show_notification(message: str, is_error: bool = False):
            try:
                sb = ft.SnackBar(
                    content=ft.Text(message, color=ft.Colors.WHITE),
                    bgcolor=ft.Colors.RED_800 if is_error else ft.Colors.GREEN_800,
                )
                page.show_dialog(sb)
            except Exception:
                logger.info(f"Notification: {message}")

        # Instantiate Views
        dashboard_view = DashboardView(app_state, on_notify=show_notification)
        accounts_view = AccountsView(app_state, on_notify=show_notification)
        downloader_view = DownloaderView(app_state, on_notify=show_notification)
        templates_view = TemplatesView(app_state, on_notify=show_notification)
        scheduler_view = SchedulerView(app_state, on_notify=show_notification)

        views = [
            dashboard_view,
            accounts_view,
            downloader_view,
            templates_view,
            scheduler_view,
        ]

        active_content_container = ft.Container(
            content=dashboard_view,
            expand=True,
        )

        def handle_refresh():
            current_view = active_content_container.content
            if hasattr(current_view, "refresh"):
                current_view.refresh()
            header.set_worker_status(app_state.is_workers_running())
            page.update()
            show_notification("Dados atualizados!", False)

        def handle_toggle_workers():
            if app_state.is_workers_running():
                app_state.stop_workers()
                header.set_worker_status(False)
                show_notification("Workers interrompidos.", False)
            else:
                app_state.start_workers()
                header.set_worker_status(True)
                show_notification("Workers iniciados em background!", False)
            page.update()

        header = AppHeader(
            title="Clip Forge V2",
            on_refresh=handle_refresh,
            on_toggle_workers=handle_toggle_workers,
            is_workers_running=app_state.is_workers_running(),
        )

        def handle_nav_change(index: int):
            if 0 <= index < len(views):
                target_view = views[index]
                active_content_container.content = target_view
                if hasattr(target_view, "refresh"):
                    target_view.refresh()
                header.set_worker_status(app_state.is_workers_running())
                page.update()

        sidebar = AppSidebar(on_destination_change=handle_nav_change)

        page.add(
            ft.Column(
                controls=[
                    header,
                    ft.Row(
                        controls=[
                            sidebar,
                            ft.VerticalDivider(width=1, color=ft.Colors.OUTLINE_VARIANT),
                            active_content_container,
                        ],
                        expand=True,
                        spacing=0,
                    ),
                ],
                expand=True,
                spacing=0,
            )
        )

    return main


def start_app(browser: bool = False, state: Optional[UIState] = None):
    """Entry point to launch the Flet Desktop App."""
    entry = create_app(state=state)
    view_mode = ft.AppView.WEB_BROWSER if browser else ft.AppView.FLET_APP
    logger.info(f"Starting Clip Forge UI in mode: {view_mode}")
    ft.run(entry, view=view_mode)


if __name__ == "__main__":
    start_app()
