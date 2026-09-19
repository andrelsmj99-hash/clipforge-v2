"""Accounts management view: YouTube OAuth, TikTok Studio, Meta Business Suite."""

from __future__ import annotations
import threading
from typing import Callable, Optional
import flet as ft

from clipforge.connectors.instagram.session import InstagramSessionManager
from clipforge.connectors.tiktok.session import TikTokSessionManager
from clipforge.connectors.youtube.auth import YouTubeAuthManager
from clipforge.connectors.youtube.session import YouTubeSessionManager
from clipforge.core.models import AccountStatus, Platform
from clipforge.ui.state import UIState


class AccountsView(ft.Container):
    def __init__(self, state: UIState, on_notify: Optional[Callable[[str, bool], None]] = None):
        self.state = state
        self.on_notify = on_notify

        self.youtube_status_text = ft.Text("Desconectado", size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.OUTLINE)
        self.youtube_detail_text = ft.Text("Nenhuma conta vinculada.", size=12, color=ft.Colors.OUTLINE)
        self.youtube_connect_btn = ft.FilledButton("Conectar YouTube (OAuth)", icon=ft.Icons.LOGIN, on_click=self._handle_connect_youtube)
        self.youtube_cookies_btn = ft.OutlinedButton("Capturar Cookies (Playwright)", icon=ft.Icons.COOKIE, on_click=self._handle_capture_cookies)

        self.tiktok_status_text = ft.Text("Desconectado", size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.OUTLINE)
        self.tiktok_detail_text = ft.Text("Sessão do TikTok Studio não detectada.", size=12, color=ft.Colors.OUTLINE)
        self.tiktok_login_btn = ft.FilledButton("Login TikTok Studio", icon=ft.Icons.WEB, on_click=self._handle_login_tiktok)

        self.instagram_status_text = ft.Text("Desconectado", size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.OUTLINE)
        self.instagram_detail_text = ft.Text("Sessão do Meta Business Suite não detectada.", size=12, color=ft.Colors.OUTLINE)
        self.instagram_login_btn = ft.FilledButton("Login Meta Business Suite", icon=ft.Icons.WEB, on_click=self._handle_login_instagram)

        super().__init__(
            content=ft.Column(
                controls=[
                    ft.Text("Contas e Sessões", size=24, weight=ft.FontWeight.BOLD),
                    ft.Text("Conecte os canais e gerencie as sessões ativas para publicação.", size=13, color=ft.Colors.OUTLINE),
                    ft.Container(height=16),
                    # YouTube Card
                    self._build_account_card(
                        title="YouTube (Google Data API v3 + Cookies)",
                        icon=ft.Icons.VIDEO_CAMERA_BACK,
                        icon_color=ft.Colors.RED,
                        status_ctrl=self.youtube_status_text,
                        detail_ctrl=self.youtube_detail_text,
                        actions=[self.youtube_connect_btn, self.youtube_cookies_btn],
                    ),
                    ft.Container(height=12),
                    # TikTok Card
                    self._build_account_card(
                        title="TikTok Studio (Automação Web)",
                        icon=ft.Icons.ONDEMAND_VIDEO,
                        icon_color=ft.Colors.CYAN,
                        status_ctrl=self.tiktok_status_text,
                        detail_ctrl=self.tiktok_detail_text,
                        actions=[self.tiktok_login_btn],
                    ),
                    ft.Container(height=12),
                    # Instagram Card
                    self._build_account_card(
                        title="Instagram / Meta Business Suite",
                        icon=ft.Icons.CAMERA_ALT,
                        icon_color=ft.Colors.PURPLE,
                        status_ctrl=self.instagram_status_text,
                        detail_ctrl=self.instagram_detail_text,
                        actions=[self.instagram_login_btn],
                    ),
                ],
                scroll=ft.ScrollMode.AUTO,
                spacing=8,
            ),
            padding=24,
            expand=True,
        )

        self.refresh()

    def _build_account_card(
        self,
        title: str,
        icon: str,
        icon_color: str,
        status_ctrl: ft.Control,
        detail_ctrl: ft.Control,
        actions: list[ft.Control],
    ) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Row([ft.Icon(icon, color=icon_color, size=28), ft.Text(title, size=16, weight=ft.FontWeight.BOLD)]),
                            status_ctrl,
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Divider(height=16, color=ft.Colors.OUTLINE_VARIANT),
                    detail_ctrl,
                    ft.Container(height=12),
                    ft.Row(controls=actions, spacing=12),
                ]
            ),
            padding=20,
            border_radius=12,
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
        )

    def refresh(self) -> None:
        accounts = self.state.get_accounts()

        # YouTube
        yt_acc = next((a for a in accounts if a.platform == Platform.YOUTUBE), None)
        if yt_acc and yt_acc.status == AccountStatus.CONNECTED:
            self.youtube_status_text.value = "Conectado"
            self.youtube_status_text.color = ft.Colors.GREEN

            health = YouTubeAuthManager.check_token_health(yt_acc)
            reason = health.get("reason") or f"{health.get('days_remaining', 0)} dias restantes"
            has_cookies = "Sim" if yt_acc.session_cookies else "Não"
            self.youtube_detail_text.value = (
                f"Canal: {yt_acc.name or yt_acc.id} | Token: {reason} | Cookies Playwright: {has_cookies}"
            )
        else:
            self.youtube_status_text.value = "Desconectado"
            self.youtube_status_text.color = ft.Colors.OUTLINE
            self.youtube_detail_text.value = "Conta YouTube não conectada. Configure as credenciais OAuth e conecte."

        # TikTok
        tt_mgr = TikTokSessionManager()
        tt_acc = next((a for a in accounts if a.platform == Platform.TIKTOK), None)
        if tt_mgr.is_connected():
            self.tiktok_status_text.value = "Sessão Ativa"
            self.tiktok_status_text.color = ft.Colors.GREEN
            name = tt_acc.name if tt_acc else "TikTok Creator"
            self.tiktok_detail_text.value = f"Sessão persistente ativa em data/sessions/tiktok ({name}). Pronto para agendar."
        else:
            self.tiktok_status_text.value = "Desconectado"
            self.tiktok_status_text.color = ft.Colors.OUTLINE
            self.tiktok_detail_text.value = "Clique no botão para abrir o navegador e fazer login no TikTok Studio."

        # Instagram
        ig_mgr = InstagramSessionManager()
        ig_acc = next((a for a in accounts if a.platform == Platform.INSTAGRAM), None)
        if ig_mgr.is_connected():
            self.instagram_status_text.value = "Sessão Ativa"
            self.instagram_status_text.color = ft.Colors.GREEN
            name = ig_acc.name if ig_acc else "Meta Business Account"
            self.instagram_detail_text.value = f"Sessão persistente ativa em data/sessions/instagram ({name}). Pronto para agendar."
        else:
            self.instagram_status_text.value = "Desconectado"
            self.instagram_status_text.color = ft.Colors.OUTLINE
            self.instagram_detail_text.value = "Clique no botão para abrir o navegador e fazer login no Meta Business Suite."

    def _handle_connect_youtube(self, _):
        def _task():
            try:
                auth_mgr = YouTubeAuthManager(self.state.db)
                auth_mgr.interactive_login()
                if self.on_notify:
                    self.on_notify("YouTube conectado com sucesso!", False)
            except Exception as e:
                if self.on_notify:
                    self.on_notify(f"Falha ao conectar YouTube: {e}", True)
            self.refresh()

        threading.Thread(target=_task, daemon=True).start()

    def _handle_capture_cookies(self, _):
        def _task():
            try:
                accounts = self.state.get_accounts()
                yt_acc = next((a for a in accounts if a.platform == Platform.YOUTUBE), None)
                acc_id = yt_acc.id if yt_acc else "yt_default"
                session_mgr = YouTubeSessionManager(self.state.db)
                session_mgr.extract_and_save_session(account_id=acc_id)
                if self.on_notify:
                    self.on_notify("Cookies capturados com sucesso!", False)
            except Exception as e:
                if self.on_notify:
                    self.on_notify(f"Erro ao capturar cookies: {e}", True)
            self.refresh()

        threading.Thread(target=_task, daemon=True).start()

    def _handle_login_tiktok(self, _):
        def _task():
            try:
                mgr = TikTokSessionManager()
                mgr.interactive_login()
                if self.on_notify:
                    self.on_notify("Sessão do TikTok Studio salva com sucesso!", False)
            except Exception as e:
                if self.on_notify:
                    self.on_notify(f"Erro no login do TikTok: {e}", True)
            self.refresh()

        threading.Thread(target=_task, daemon=True).start()

    def _handle_login_instagram(self, _):
        def _task():
            try:
                mgr = InstagramSessionManager()
                mgr.interactive_login()
                if self.on_notify:
                    self.on_notify("Sessão do Meta Business Suite salva com sucesso!", False)
            except Exception as e:
                if self.on_notify:
                    self.on_notify(f"Erro no login do Instagram/Meta: {e}", True)
            self.refresh()

        threading.Thread(target=_task, daemon=True).start()
