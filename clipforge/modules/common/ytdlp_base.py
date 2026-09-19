"""
Shared yt-dlp-based downloader, reused by YouTube, TikTok and Instagram.

Decisão do planejamento: consolidar o Downloader em cima do yt-dlp **como
biblioteca Python** (não CLI/subprocess), com deduplicação em duas camadas:
checagem no banco (feita pelo worker/CLI) + `download_archive` nativo do
yt-dlp (feito aqui) como camada extra de segurança.

Cada plataforma tem um extractor de maturidade diferente:
- YouTube: extractor mais maduro e ativo.
- TikTok: extractor dedicado e ativo.
- Instagram: extractor dedicado, mas **frágil** — quebra periodicamente com
  mudanças da plataforma. Erros inesperados aqui devem trazer uma mensagem
  clara apontando essa fragilidade conhecida, não uma falha silenciosa.
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
import yt_dlp

from clipforge.core.models import VideoKind
from clipforge.modules.youtube.scraper import classify_video_kind

logger = logging.getLogger(__name__)


class DownloadError(Exception):
    """Base exception for any yt-dlp-backed download failure."""
    pass


class AgeRestrictedError(DownloadError):
    """Raised when a video requires login/cookies due to age restriction."""
    pass


class VideoUnavailableError(DownloadError):
    """Raised when a video is private, deleted, or geo-blocked."""
    pass


class ExtractorFragileError(DownloadError):
    """
    Raised for platforms with known-fragile extractors (Instagram) when an
    unexpected error occurs — signals "this may be the extractor breaking
    again", not necessarily a problem with the specific video.
    """
    pass


def get_ffmpeg_path() -> Optional[str]:
    """Locate ffmpeg executable from imageio_ffmpeg or system PATH."""
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        if ffmpeg_exe and Path(ffmpeg_exe).exists():
            return str(ffmpeg_exe)
    except Exception:
        pass

    import shutil
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg and Path(sys_ffmpeg).exists():
        return str(sys_ffmpeg)

    return None


class YtDlpDownloaderBase:
    """
    Generic yt-dlp download logic. Platform subclasses only need to override
    `platform_name` and `_classify_error` to plug in platform-specific error
    string matching.
    """

    platform_name: str = "generic"

    def __init__(
        self,
        downloads_base_dir: Path | str,
        cookies_path: Optional[Path | str] = None,
        download_archive_path: Optional[Path | str] = None,
    ):
        self.downloads_base_dir = Path(downloads_base_dir)
        self.cookies_path = Path(cookies_path) if cookies_path else None
        self.download_archive_path = Path(download_archive_path) if download_archive_path else None
        self.downloads_base_dir.mkdir(parents=True, exist_ok=True)
        if self.download_archive_path:
            self.download_archive_path.parent.mkdir(parents=True, exist_ok=True)

    def _classify_error(self, err_msg: str, url: str) -> DownloadError:
        """
        Map a lowercased yt-dlp error message to a specific exception.
        Subclasses override to add platform-specific patterns; falls back
        to the patterns common across platforms.
        """
        if "sign in to confirm your age" in err_msg or "age-restricted" in err_msg:
            return AgeRestrictedError(f"Age restriction encountered for {url}. Session cookies required.")
        if "private video" in err_msg or "members-only" in err_msg or "this video is unavailable" in err_msg:
            return VideoUnavailableError(f"Video unavailable or private: {url}")
        if "not available in your country" in err_msg or "geo" in err_msg and "block" in err_msg:
            return VideoUnavailableError(f"Video geo-blocked: {url}")
        return DownloadError(f"Download failed for {url}: {err_msg}")

    def download(
        self,
        url: str,
        channel_subfolder: Optional[str] = None,
        progress_hook: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Tuple[Path, Dict[str, Any]]:
        """
        Download a single video to local disk.
        Returns: (local_file_path, metadata_dict)
        """
        target_dir = self.downloads_base_dir
        if channel_subfolder:
            target_dir = target_dir / channel_subfolder
        target_dir.mkdir(parents=True, exist_ok=True)

        out_template = str(target_dir / "%(id)s.%(ext)s")

        ffmpeg_bin = get_ffmpeg_path()

        ydl_opts: Dict[str, Any] = {
            "outtmpl": out_template,
            "quiet": True,
            "no_warnings": True,
            "overwrites": False,  # Skip if already downloaded on disk
        }

        if ffmpeg_bin:
            ydl_opts["ffmpeg_location"] = ffmpeg_bin
            ydl_opts["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
            ydl_opts["merge_output_format"] = "mp4"
        else:
            # Fallback when ffmpeg is missing to prevent aborting on merge
            ydl_opts["format"] = "best[ext=mp4]/best"


        if self.cookies_path and self.cookies_path.exists():
            ydl_opts["cookiefile"] = str(self.cookies_path)

        if self.download_archive_path:
            # Camada extra de deduplicação (por ID de vídeo), além da
            # checagem por source_url já feita no banco pelo worker/CLI.
            ydl_opts["download_archive"] = str(self.download_archive_path)

        if progress_hook:
            ydl_opts["progress_hooks"] = [progress_hook]

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if not info:
                    raise DownloadError(f"No info extracted for {url}")

                filename = ydl.prepare_filename(info)
                file_path = Path(filename)

                if not file_path.exists() and file_path.with_suffix(".mp4").exists():
                    file_path = file_path.with_suffix(".mp4")

                if not file_path.exists():
                    vid_id = info.get("id")
                    matches = list(target_dir.glob(f"{vid_id}.*"))
                    if matches:
                        file_path = matches[0]
                    else:
                        raise DownloadError(f"Downloaded file not found for {url}")

                metadata = {
                    "id": info.get("id"),
                    "title": info.get("title"),
                    "duration_seconds": info.get("duration"),
                    "width": info.get("width"),
                    "height": info.get("height"),
                    "kind": classify_video_kind(
                        info.get("duration"),
                        info.get("width"),
                        info.get("height"),
                        url,
                    ),
                    "channel_name": info.get("uploader") or info.get("channel"),
                    "channel_id": info.get("channel_id"),
                    "upload_date": info.get("upload_date"),
                }

                return file_path, metadata

        except yt_dlp.utils.DownloadError as e:
            raise self._classify_error(str(e).lower(), url) from e
        except DownloadError:
            raise
        except Exception as e:
            raise DownloadError(f"Unexpected download error for {url} ({self.platform_name}): {e}") from e
