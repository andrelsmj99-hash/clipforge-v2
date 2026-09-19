"""YouTube Channel and Video scraper using yt-dlp."""

from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional
import yt_dlp

from clipforge.core.models import VideoKind

logger = logging.getLogger(__name__)


def classify_video_kind(
    duration: Optional[float] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    url: Optional[str] = None,
    source_tab: Optional[str] = None,
) -> VideoKind:
    """
    Classify a video as Short, Long or Live VOD.

    Heurística:
    - `source_tab == "streams"` (aba de lives encerradas do YouTube) -> LIVE_VOD,
      sem depender de duração (lives costumam durar horas, mas a origem já entrega a resposta).
    - URL explicitamente contém '/shorts/' -> SHORT
    - Duração <= 60s -> SHORT. Nota: o YouTube passou a aceitar Shorts em
      qualquer proporção (não só vertical) desde a mudança de política de
      2024, então a proporção (largura/altura) não é usada como filtro
      adicional aqui — é apenas informativa nos metadados salvos.
    - Duração > 60s -> LONG
    - Duração desconhecida e fora da aba de shorts/streams -> UNKNOWN
    """
    if source_tab == "streams":
        return VideoKind.LIVE_VOD

    if url and "/shorts/" in url:
        return VideoKind.SHORT

    if duration is not None:
        return VideoKind.SHORT if duration <= 60.0 else VideoKind.LONG

    return VideoKind.UNKNOWN


class YouTubeScraper:
    def __init__(self, cookies_path: Optional[str] = None):
        self.cookies_path = cookies_path

    def _get_ydl_options(self, extra_opts: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "skip_download": True,
            "ignoreerrors": True,
        }
        if self.cookies_path:
            opts["cookiefile"] = self.cookies_path
        if extra_opts:
            opts.update(extra_opts)
        return opts

    def _build_tab_url(self, channel_url: str, tab: str) -> str:
        """
        Build a channel URL pointing at a specific tab (videos/shorts/streams).
        If the caller already passed a full URL with an explicit tab suffix,
        respect it as-is instead of overriding.
        """
        clean_url = channel_url.strip()
        if clean_url.startswith("http://") or clean_url.startswith("https://"):
            if any(clean_url.rstrip("/").endswith(f"/{t}") for t in ("videos", "shorts", "streams")):
                return clean_url
            return f"{clean_url.rstrip('/')}/{tab}"

        handle = clean_url if clean_url.startswith("@") else f"@{clean_url}"
        return f"https://www.youtube.com/{handle}/{tab}"

    def list_channel_videos(
        self,
        channel_url: str,
        max_results: Optional[int] = None,
        only_kind: Optional[VideoKind] = None,
        tab: str = "videos",
        only_shorts: bool = False,
        only_lives: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Extract video list and metadata from one tab of a channel
        (/videos, /shorts or /streams) without downloading media.

        Nota: o YouTube separa o conteúdo do canal em abas distintas —
        apontar só pra `/@canal` sem sufixo resolve, por padrão, pra aba de
        Vídeos. Shorts e lives (`/streams`) não entram automaticamente;
        use `tab="shorts"` / `tab="streams"` ou `list_channel_all_tabs()`
        para cobrir todo o escopo decidido no planejamento.
        """
        if only_shorts:
            tab = "shorts"
            only_kind = VideoKind.SHORT
        elif only_lives:
            tab = "streams"
            only_kind = VideoKind.LIVE_VOD

        clean_url = self._build_tab_url(channel_url, tab)
        source_tab = tab if tab in ("videos", "shorts", "streams") else None

        ydl_opts = self._get_ydl_options({
            "playlist_items": f"1-{max_results}" if max_results else None,
            "extract_flat": "in_playlist",
        })

        results: List[Dict[str, Any]] = []

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(clean_url, download=False)
            except Exception as e:
                logger.error(f"Error fetching channel metadata for {clean_url}: {e}")
                raise

            if not info:
                return []

            entries = info.get("entries") or [info]
            for entry in entries:
                if not entry or not isinstance(entry, dict):
                    continue

                vid_id = entry.get("id")
                if not vid_id:
                    continue

                url = entry.get("url") or entry.get("webpage_url") or f"https://www.youtube.com/watch?v={vid_id}"
                duration = entry.get("duration")
                width = entry.get("width")
                height = entry.get("height")
                title = entry.get("title") or f"Video {vid_id}"

                kind = classify_video_kind(
                    duration=duration,
                    width=width,
                    height=height,
                    url=url,
                    source_tab=source_tab,
                )

                if only_kind and only_kind != VideoKind.UNKNOWN and kind != only_kind:
                    continue

                results.append({
                    "id": vid_id,
                    "title": title,
                    "url": url,
                    "duration_seconds": duration,
                    "width": width,
                    "height": height,
                    "kind": kind,
                    "source_tab": source_tab,
                    "view_count": entry.get("view_count"),
                    "upload_date": entry.get("upload_date"),
                    "channel_name": entry.get("channel") or info.get("title") or info.get("uploader"),
                    "channel_id": entry.get("channel_id") or info.get("id"),
                })

                if max_results and len(results) >= max_results:
                    break

        return results

    def list_channel_all_tabs(
        self,
        channel_url: str,
        max_results_per_tab: Optional[int] = None,
        include_lives: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Lista o canal cobrindo o escopo decidido no planejamento: vídeos
        normais + shorts + (opcionalmente) lives encerradas (VODs).

        Cada aba é consultada explicitamente (`/videos`, `/shorts`,
        `/streams`) e os resultados são deduplicados por ID de vídeo — um
        mesmo vídeo não deveria aparecer em mais de uma aba, mas isso
        protege contra qualquer sobreposição.
        """
        tabs = ["videos", "shorts"] + (["streams"] if include_lives else [])
        seen_ids: set = set()
        merged: List[Dict[str, Any]] = []

        for tab in tabs:
            try:
                tab_results = self.list_channel_videos(
                    channel_url, max_results=max_results_per_tab, tab=tab
                )
            except Exception as e:
                logger.error(f"Error listing tab '{tab}' for {channel_url}: {e}")
                continue

            for v in tab_results:
                if v["id"] in seen_ids:
                    continue
                seen_ids.add(v["id"])
                merged.append(v)

        return merged

    def get_video_info(self, video_url: str) -> Optional[Dict[str, Any]]:
        """Fetch detailed information for a single video."""
        ydl_opts = self._get_ydl_options({"extract_flat": False})
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(video_url, download=False)
                if not info:
                    return None
                duration = info.get("duration")
                width = info.get("width")
                height = info.get("height")
                url = info.get("webpage_url") or video_url

                return {
                    "id": info.get("id"),
                    "title": info.get("title"),
                    "url": url,
                    "duration_seconds": duration,
                    "width": width,
                    "height": height,
                    "kind": classify_video_kind(duration, width, height, url),
                    "description": info.get("description"),
                    "tags": info.get("tags") or [],
                    "upload_date": info.get("upload_date"),
                    "channel_name": info.get("uploader") or info.get("channel"),
                    "channel_id": info.get("channel_id"),
                    "age_limit": info.get("age_limit", 0),
                }
            except Exception as e:
                logger.error(f"Error extracting video info for {video_url}: {e}")
                return None
