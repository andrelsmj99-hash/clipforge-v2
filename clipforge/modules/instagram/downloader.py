"""
Instagram video downloader via yt-dlp.

Risco conhecido (planejamento, seção 3.2/6): o extractor do Instagram no
yt-dlp é dedicado, mas frágil — quebra periodicamente com mudanças da
plataforma (houve issue de extração de perfil público quebrada recentemente).
Por isso, qualquer erro não reconhecido aqui é reportado como
ExtractorFragileError, com uma mensagem que aponta a causa provável em vez de
deixar o worker reportar um traceback genérico — quem for investigar a falha
sabe por onde começar (checar versão do yt-dlp / issues abertos) sem
depender 100% deste caminho no fluxo crítico.
"""

from __future__ import annotations
from clipforge.core.config import DOWNLOADS_DIR
from clipforge.modules.common.ytdlp_base import (
    DownloadError,
    ExtractorFragileError,
    VideoUnavailableError,
    YtDlpDownloaderBase,
)


class InstagramDownloader(YtDlpDownloaderBase):
    platform_name = "instagram"

    def __init__(self, downloads_base_dir=DOWNLOADS_DIR / "instagram", cookies_path=None, download_archive_path=None):
        super().__init__(
            downloads_base_dir=downloads_base_dir,
            cookies_path=cookies_path,
            download_archive_path=download_archive_path,
        )

    def _classify_error(self, err_msg: str, url: str) -> DownloadError:
        if "private" in err_msg or "login required" in err_msg or "no video formats found" in err_msg:
            return VideoUnavailableError(f"Instagram content unavailable/private or login-gated: {url}")
        # Erro não reconhecido nas categorias conhecidas: assume que pode ser
        # o extractor do Instagram quebrado por mudança recente da plataforma.
        return ExtractorFragileError(
            f"Instagram download failed for {url} — pode ser o extractor do "
            f"yt-dlp quebrado por mudança recente do Instagram (extractor "
            f"conhecido por ser frágil). Detalhe: {err_msg}"
        )
