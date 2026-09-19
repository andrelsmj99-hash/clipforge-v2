"""Clip Forge V2 - Command Line Interface (CLI) powered by Rich and Click."""

from __future__ import annotations
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table

from clipforge.connectors.instagram.session import InstagramSessionManager
from clipforge.connectors.tiktok.session import TikTokSessionManager
from clipforge.connectors.youtube.auth import YouTubeAuthManager
from clipforge.connectors.youtube.session import YouTubeSessionManager
from clipforge.core.config import DEFAULT_YOUTUBE_CLIENT_SECRET_FILE
from clipforge.core.db import Database
from clipforge.core.models import (
    AccountStatus,
    JobStatus,
    JobType,
    Platform,
    Post,
    PostStatus,
    Render,
    RenderStatus,
    Template,
    Video,
    VideoKind,
    VideoStatus,
)
from clipforge.core.queue import JobQueue
from clipforge.modules.agendador.due_scanner import DueScanner
from clipforge.modules.agendador.scheduler import NATIVE_SCHEDULE_ON_UPLOAD, BatchScheduler
from clipforge.modules.editor.renderer import CanvaRenderer
from clipforge.modules.instagram.scraper import InstagramScraper
from clipforge.modules.templates.manager import TemplateManager
from clipforge.modules.templates.mapper import TemplateMapper
from clipforge.modules.tiktok.scraper import TikTokScraper
from clipforge.modules.youtube.scraper import YouTubeScraper
from clipforge.workers.downloader import DownloaderWorker
from clipforge.workers.publisher import PublisherWorker
from clipforge.workers.runner import WorkerRunner

# Initialize rich console
console = Console()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
)


@click.group()
def main() -> None:
    """Clip Forge V2 — Multi-Platform Video Automation Engine."""
    pass


# -----------------------------------------------------------------------------
# ACCOUNTS COMMANDS
# -----------------------------------------------------------------------------
@main.group()
def accounts() -> None:
    """Manage connected social media accounts."""
    pass


@accounts.command("list")
def list_accounts() -> None:
    """List all configured social media accounts and their token health."""
    db = Database()
    auth_mgr = YouTubeAuthManager(db)
    accs = db.list_accounts()

    if not accs:
        console.print("[yellow]No accounts found. Use 'clipforge accounts connect-youtube' to add one.[/yellow]")
        return

    table = Table(title="Connected Social Accounts", header_style="bold cyan")
    table.add_column("ID", style="dim")
    table.add_column("Platform", style="bold")
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Token Health / Expiry")
    table.add_column("Cookies")

    for acc in accs:
        status_color = "green" if acc.status == AccountStatus.CONNECTED else "red"
        status_str = f"[{status_color}]{acc.status.value}[/{status_color}]"

        # Check token health
        health = auth_mgr.check_token_health(acc)
        if health["is_expired"]:
            token_str = "[bold red]EXPIRED (Reconnect required)[/bold red]"
        elif health.get("warning"):
            token_str = f"[bold yellow]{health['days_remaining']}d remaining (Warning)[/bold yellow]"
        elif health["days_remaining"] is not None:
            token_str = f"[green]{health['days_remaining']}d remaining[/green]"
        else:
            token_str = "[dim]N/A[/dim]"

        cookies_str = "[green]Yes[/green]" if acc.session_cookies else "[dim]No[/dim]"

        table.add_row(
            acc.id,
            acc.platform.value.upper(),
            acc.name or "[dim]Unnamed[/dim]",
            status_str,
            token_str,
            cookies_str,
        )

    console.print(table)


@accounts.command("connect-youtube")
@click.option(
    "--client-secret",
    type=click.Path(exists=True, path_type=Path),
    default=DEFAULT_YOUTUBE_CLIENT_SECRET_FILE,
    help="Path to Google Cloud client_secret_*.json",
)
@click.option("--port", default=8080, help="Local loopback port for OAuth redirect")
def connect_youtube(client_secret: Path, port: int) -> None:
    """Connect a YouTube account via OAuth 2.0 (Google Data API v3)."""
    db = Database()
    auth_mgr = YouTubeAuthManager(db)

    console.print(Panel(
        f"[bold cyan]YouTube OAuth 2.0 Connection[/bold cyan]\n"
        f"Client Secret File: [magenta]{client_secret}[/magenta]\n"
        f"Loopback Port: [magenta]{port}[/magenta]\n"
        f"[dim]Note: In GCP Testing mode, this token is valid for 7 days.[/dim]",
        title="Account Connection",
    ))

    try:
        creds, profile = auth_mgr.start_oauth_flow(client_secrets_path=client_secret, port=port)
        acc = auth_mgr.register_account(credentials=creds, profile=profile, is_testing_mode=True)
        console.print(f"[bold green]Successfully connected channel: {acc.name} (Account ID: {acc.id})[/bold green]")
    except Exception as e:
        console.print(f"[bold red]Failed to connect YouTube account:[/bold red] {e}")
        sys.exit(1)


@accounts.command("capture-youtube-session")
@click.option("--account-id", help="Optional YouTube account ID to link session cookies to")
def capture_youtube_session(account_id: Optional[str]) -> None:
    """Launch interactive browser to capture YouTube cookies for age-restricted videos."""
    db = Database()
    session_mgr = YouTubeSessionManager(db)

    console.print("[cyan]Opening browser for YouTube authentication and cookie extraction...[/cyan]")
    try:
        session_mgr.extract_and_save_session(account_id=account_id)
        console.print("[bold green]Cookies successfully captured and saved![/bold green]")
    except Exception as e:
        console.print(f"[bold red]Failed to capture session:[/bold red] {e}")
        sys.exit(1)


@accounts.command("login-tiktok")
def login_tiktok() -> None:
    """Launch interactive browser to log in to TikTok / TikTok Studio and persist session."""
    session_mgr = TikTokSessionManager()
    console.print("[cyan]Opening browser for TikTok login...[/cyan]")
    if session_mgr.interactive_login():
        console.print("[bold green]TikTok session successfully authenticated and saved![/bold green]")
    else:
        console.print("[bold red]TikTok login was not completed or timed out.[/bold red]")


@accounts.command("login-instagram")
def login_instagram() -> None:
    """Launch interactive browser to log in to Meta Business Suite / Instagram and persist session."""
    session_mgr = InstagramSessionManager()
    console.print("[cyan]Opening browser for Meta Business Suite / Instagram login...[/cyan]")
    if session_mgr.interactive_login():
        console.print("[bold green]Meta Business Suite session successfully authenticated and saved![/bold green]")
    else:
        console.print("[bold red]Meta Business Suite login was not completed or timed out.[/bold red]")


# -----------------------------------------------------------------------------
# YOUTUBE SCRAPER & ENQUEUE COMMANDS
# -----------------------------------------------------------------------------
@main.group()
def youtube() -> None:
    """Scrape, download, and manage YouTube videos."""
    pass


@youtube.command("list-channel")
@click.argument("channel_url")
@click.option("--max-results", "-n", default=20, help="Maximum videos to list per tab")
@click.option("--only-shorts", is_flag=True, help="Filter for Shorts only")
@click.option("--only-longs", is_flag=True, help="Filter for Long videos only")
@click.option("--only-lives", is_flag=True, help="Filter for Live VODs only")
@click.option("--no-lives", is_flag=True, help="Exclude the /streams tab (lives encerradas)")
def list_channel(channel_url: str, max_results: int, only_shorts: bool, only_longs: bool, only_lives: bool, no_lives: bool) -> None:
    """List videos from a YouTube channel (vídeos + shorts + lives encerradas) without downloading."""
    scraper = YouTubeScraper()
    if only_shorts:
        only_kind = VideoKind.SHORT
    elif only_longs:
        only_kind = VideoKind.LONG
    elif only_lives:
        only_kind = VideoKind.LIVE_VOD
    else:
        only_kind = None

    console.print(f"[cyan]Fetching metadata for {channel_url} (vídeos + shorts{' + streams' if not no_lives else ''})...[/cyan]")
    try:
        videos = scraper.list_channel_all_tabs(
            channel_url, max_results_per_tab=max_results, include_lives=not no_lives
        )
        if only_kind:
            videos = [v for v in videos if v["kind"] == only_kind]
    except Exception as e:
        console.print(f"[bold red]Failed to list channel:[/bold red] {e}")
        sys.exit(1)

    if not videos:
        console.print("[yellow]No videos found matching criteria.[/yellow]")
        return

    table = Table(title=f"Videos from {channel_url}", header_style="bold magenta")
    table.add_column("ID", style="dim")
    table.add_column("Title")
    table.add_column("Kind", style="bold")
    table.add_column("Duration")
    table.add_column("Resolution")
    table.add_column("URL", style="blue")

    kind_colors = {VideoKind.SHORT: "magenta", VideoKind.LONG: "cyan", VideoKind.LIVE_VOD: "red"}

    for v in videos:
        dur = f"{int(v['duration_seconds'])}s" if v.get("duration_seconds") is not None else "N/A"
        res = f"{v.get('width', '?')}x{v.get('height', '?')}" if v.get("width") else "N/A"
        color = kind_colors.get(v["kind"], "white")
        kind_badge = f"[{color}]{v['kind'].value.upper()}[/{color}]"

        table.add_row(
            v["id"],
            v["title"][:50] + ("..." if len(v["title"]) > 50 else ""),
            kind_badge,
            dur,
            res,
            v["url"],
        )

    console.print(table)


@youtube.command("enqueue-download")
@click.argument("channel_url")
@click.option("--max-results", "-n", default=10, help="Maximum videos to enqueue per tab")
@click.option("--only-shorts", is_flag=True, help="Enqueue Shorts only")
@click.option("--only-longs", is_flag=True, help="Enqueue Long videos only")
@click.option("--only-lives", is_flag=True, help="Enqueue Live VODs only")
@click.option("--no-lives", is_flag=True, help="Exclude the /streams tab (lives encerradas)")
@click.option("--account-id", help="Optional account ID to use for session cookies")
def enqueue_download(
    channel_url: str,
    max_results: int,
    only_shorts: bool,
    only_longs: bool,
    only_lives: bool,
    no_lives: bool,
    account_id: Optional[str],
) -> None:
    """Scrape channel (vídeos + shorts + lives encerradas) and enqueue download jobs with automatic deduplication."""
    db = Database()
    queue = JobQueue(db)
    scraper = YouTubeScraper()

    if only_shorts:
        only_kind = VideoKind.SHORT
    elif only_longs:
        only_kind = VideoKind.LONG
    elif only_lives:
        only_kind = VideoKind.LIVE_VOD
    else:
        only_kind = None

    console.print(f"[cyan]Scanning {channel_url} for videos (vídeos + shorts{' + streams' if not no_lives else ''})...[/cyan]")
    try:
        videos = scraper.list_channel_all_tabs(
            channel_url, max_results_per_tab=max_results, include_lives=not no_lives
        )
        if only_kind:
            videos = [v for v in videos if v["kind"] == only_kind]
    except Exception as e:
        console.print(f"[bold red]Failed to scan channel:[/bold red] {e}")
        sys.exit(1)

    enqueued_count = 0
    skipped_count = 0

    for v in videos:
        source_url = v["url"]
        # Deduplication check
        existing = db.get_video_by_source_url(source_url)
        if existing and existing.status == VideoStatus.DOWNLOADED:
            console.print(f"[dim]Skipping already downloaded video: {v['title']}[/dim]")
            skipped_count += 1
            continue

        # Save or update video entry
        video_id = existing.id if existing else f"vid_{v['id']}"
        video = Video(
            id=video_id,
            channel_id=v.get("channel_id"),
            source_url=source_url,
            title=v.get("title"),
            kind=v.get("kind", VideoKind.UNKNOWN),
            duration_seconds=v.get("duration_seconds"),
            width=v.get("width"),
            height=v.get("height"),
            status=VideoStatus.PENDING,
        )
        db.save_video(video)

        # Enqueue download job
        queue.enqueue(
            job_type=JobType.DOWNLOAD,
            payload={
                "video_id": video_id,
                "source_url": source_url,
                "channel_id": v.get("channel_id"),
                "account_id": account_id,
                "platform": Platform.YOUTUBE.value,
                "use_cookies": True,
            },
        )
        enqueued_count += 1
        console.print(f"[green]+ Enqueued download job for:[/green] {v['title']} ({v['kind'].value})")

    console.print(Panel(
        f"[bold green]Enqueued: {enqueued_count} jobs[/bold green]\n"
        f"[yellow]Skipped (Deduplicated): {skipped_count} videos[/yellow]",
        title="Enqueue Summary",
    ))


@youtube.command("schedule-upload")
@click.argument("video_path", type=click.Path(exists=True, path_type=Path))
@click.option("--account-id", "-a", required=True, help="Connected YouTube Account ID")
@click.option("--title", "-t", required=True, help="Video title")
@click.option("--caption", "-c", default="", help="Video description / caption")
@click.option("--tags", default="", help="Comma-separated tags (e.g. 'shorts,gaming,viral')")
@click.option(
    "--publish-at",
    help="Target UTC publish date/time (ISO 8601 format, e.g. 2026-09-02T15:00:00Z). If omitted, publishes immediately.",
)
@click.option("--made-for-kids", is_flag=True, default=False, help="Set madeForKids flag")
def schedule_upload(
    video_path: Path,
    account_id: str,
    title: str,
    caption: str,
    tags: str,
    publish_at: Optional[str],
    made_for_kids: bool,
) -> None:
    """Create a post and enqueue a YouTube upload job with native scheduling (publishAt)."""
    db = Database()
    queue = JobQueue(db)

    # Verify account
    account = db.get_account(account_id)
    if not account:
        console.print(f"[bold red]Account {account_id} not found.[/bold red]")
        sys.exit(1)

    if publish_at:
        try:
            scheduled_dt = datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
        except ValueError:
            console.print("[bold red]Invalid --publish-at format. Use ISO 8601 (e.g., 2026-09-02T15:00:00Z)[/bold red]")
            sys.exit(1)
    else:
        scheduled_dt = datetime.now(timezone.utc)

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    post_id = f"post_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    post = Post(
        id=post_id,
        account_id=account_id,
        title=title,
        caption=caption,
        tags=tag_list,
        scheduled_at=scheduled_dt,
        status=PostStatus.DRAFT,
    )
    db.save_post(post)

    # Enqueue publish job
    job = queue.enqueue(
        job_type=JobType.PUBLISH,
        payload={
            "post_id": post_id,
            "account_id": account_id,
            "platform": Platform.YOUTUBE.value,
            "video_path": str(video_path.resolve()),
            "made_for_kids": made_for_kids,
        },
    )

    console.print(Panel(
        f"[bold green]Created Post: {post_id}[/bold green]\n"
        f"Video File: [magenta]{video_path}[/magenta]\n"
        f"Account: [cyan]{account.name} ({account_id})[/cyan]\n"
        f"Scheduled For: [yellow]{scheduled_dt.isoformat()}[/yellow]\n"
        f"Enqueued Publish Job: [cyan]{job.id}[/cyan]",
        title="Upload Enqueued",
    ))


# -----------------------------------------------------------------------------
# TIKTOK / INSTAGRAM SCRAPER & ENQUEUE COMMANDS
# -----------------------------------------------------------------------------
_PROFILE_SCRAPERS = {
    "tiktok": (TikTokScraper, Platform.TIKTOK),
    "instagram": (InstagramScraper, Platform.INSTAGRAM),
}
_DISPLAY_NAMES = {"tiktok": "TikTok", "instagram": "Instagram"}


def _register_profile_platform_group(name: str) -> None:
    scraper_cls, platform = _PROFILE_SCRAPERS[name]
    display_names = _DISPLAY_NAMES

    @main.group(name=name, help=f"Scrape and download {display_names[name]} videos (via yt-dlp).")
    def platform_group() -> None:
        pass

    @platform_group.command("list-profile", help=f"List videos from a {display_names[name]} profile without downloading.")
    @click.argument("profile")
    @click.option("--max-results", "-n", default=20, help="Maximum videos to list")
    def list_profile(profile: str, max_results: int) -> None:
        scraper = scraper_cls()
        profile_url = scraper_cls.normalize_profile_url(profile)

        console.print(f"[cyan]Fetching metadata for {profile_url}...[/cyan]")
        try:
            videos = scraper.list_profile_videos(profile_url, max_results=max_results)
        except Exception as e:
            console.print(
                f"[bold red]Failed to list {name} profile:[/bold red] {e}\n"
                + ("[yellow]Extractor do Instagram é conhecido por ser frágil — pode ser quebra temporária.[/yellow]" if name == "instagram" else "")
            )
            sys.exit(1)

        if not videos:
            console.print("[yellow]No videos found.[/yellow]")
            return

        table = Table(title=f"Videos from {profile_url}", header_style="bold magenta")
        table.add_column("ID", style="dim")
        table.add_column("Title")
        table.add_column("Duration")
        table.add_column("URL", style="blue")

        for v in videos:
            dur = f"{int(v['duration_seconds'])}s" if v.get("duration_seconds") is not None else "N/A"
            table.add_row(v["id"], (v["title"] or "")[:50], dur, v["url"] or "")

        console.print(table)

    @platform_group.command("enqueue-download", help=f"Scrape a {display_names[name]} profile and enqueue download jobs with deduplication.")
    @click.argument("profile")
    @click.option("--max-results", "-n", default=10, help="Maximum videos to enqueue")
    def enqueue_profile_download(profile: str, max_results: int) -> None:
        db = Database()
        jq = JobQueue(db)
        scraper = scraper_cls()
        profile_url = scraper_cls.normalize_profile_url(profile)

        console.print(f"[cyan]Scanning {profile_url} for videos...[/cyan]")
        try:
            videos = scraper.list_profile_videos(profile_url, max_results=max_results)
        except Exception as e:
            console.print(
                f"[bold red]Failed to scan {name} profile:[/bold red] {e}\n"
                + ("[yellow]Extractor do Instagram é conhecido por ser frágil — pode ser quebra temporária.[/yellow]" if name == "instagram" else "")
            )
            sys.exit(1)

        enqueued_count = 0
        skipped_count = 0

        for v in videos:
            source_url = v["url"]
            if not source_url:
                continue
            existing = db.get_video_by_source_url(source_url)
            if existing and existing.status == VideoStatus.DOWNLOADED:
                skipped_count += 1
                continue

            video_id = existing.id if existing else f"vid_{name}_{v['id']}"
            video = Video(
                id=video_id,
                channel_id=v.get("channel_id"),
                source_url=source_url,
                title=v.get("title"),
                kind=v.get("kind", VideoKind.UNKNOWN),
                duration_seconds=v.get("duration_seconds"),
                width=v.get("width"),
                height=v.get("height"),
                status=VideoStatus.PENDING,
            )
            db.save_video(video)

            jq.enqueue(
                job_type=JobType.DOWNLOAD,
                payload={
                    "video_id": video_id,
                    "source_url": source_url,
                    "channel_id": v.get("channel_id"),
                    "platform": platform.value,
                    "use_cookies": False,
                },
            )
            enqueued_count += 1
            console.print(f"[green]+ Enqueued download job for:[/green] {v['title']}")

        console.print(Panel(
            f"[bold green]Enqueued: {enqueued_count} jobs[/bold green]\n"
            f"[yellow]Skipped (Deduplicated): {skipped_count} videos[/yellow]",
            title="Enqueue Summary",
        ))

    @platform_group.command("schedule-upload", help=f"Upload and schedule a video natively to {display_names[name]}.")
    @click.argument("video_path", type=click.Path(exists=True, path_type=Path))
    @click.option("--account-id", required=True, help="Connected account ID")
    @click.option("--title", default="", help="Video title")
    @click.option("--caption", default="", help="Post caption")
    @click.option("--tags", default="", help="Comma-separated tags")
    @click.option("--publish-at", help="Target publish time in ISO 8601 format (e.g. 2026-09-25T18:00:00Z)")
    def schedule_upload(
        video_path: Path,
        account_id: str,
        title: str,
        caption: str,
        tags: str,
        publish_at: Optional[str],
    ) -> None:
        db = Database()
        queue = JobQueue(db)

        account = db.get_account(account_id)
        if not account:
            console.print(f"[bold red]Account {account_id} not found.[/bold red]")
            sys.exit(1)

        if publish_at:
            try:
                scheduled_dt = datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
            except ValueError:
                console.print("[bold red]Invalid --publish-at format. Use ISO 8601 (e.g., 2026-09-25T18:00:00Z)[/bold red]")
                sys.exit(1)
        else:
            scheduled_dt = datetime.now(timezone.utc)

        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        post_id = f"post_{platform.value}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        post = Post(
            id=post_id,
            account_id=account_id,
            platform=platform,
            title=title or video_path.stem,
            caption=caption,
            tags=tag_list,
            scheduled_at=scheduled_dt,
            status=PostStatus.DRAFT,
        )
        db.save_post(post)

        job = queue.enqueue(
            job_type=JobType.PUBLISH,
            payload={
                "post_id": post_id,
                "account_id": account_id,
                "platform": platform.value,
                "video_path": str(video_path.resolve()),
                "publish_at": scheduled_dt.isoformat(),
            },
        )

        console.print(Panel(
            f"[bold green]Created Post: {post_id}[/bold green]\n"
            f"Platform: [bold magenta]{display_names[name]}[/bold magenta]\n"
            f"Video File: [magenta]{video_path}[/magenta]\n"
            f"Account: [cyan]{account.name or account_id}[/cyan]\n"
            f"Scheduled For: [yellow]{scheduled_dt.isoformat()}[/yellow]\n"
            f"Enqueued Publish Job: [cyan]{job.id}[/cyan]",
            title="Upload Enqueued",
        ))


for _platform_name in _PROFILE_SCRAPERS:
    _register_profile_platform_group(_platform_name)


# -----------------------------------------------------------------------------
# AGENDADOR — BATCH SCHEDULING COMMANDS
# -----------------------------------------------------------------------------
@main.group()
def batch() -> None:
    """Postagem em massa: agrupa vídeos, define legenda e intervalo entre posts."""
    pass


@batch.command("create")
@click.argument("video_ids", nargs=-1, required=False)
@click.option("--render-id", "-r", "render_ids", multiple=True, help="Render ID(s) do Canva/Editor para postagem")
@click.option("--account-id", required=True, help="Conta de destino (account_id já conectado)")
@click.option("--platform", "platform_str", required=True, type=click.Choice([p.value for p in Platform]), help="Plataforma de destino")
@click.option("--caption", required=True, help="Legenda (aceita {n} e {total}, ex: 'Parte {n}/{total}')")
@click.option("--interval-minutes", default=60, help="Intervalo entre posts, em minutos (padrão: 60)")
@click.option("--start-at", default=None, help="Início do lote em ISO 8601 (padrão: agora)")
@click.option("--title", default=None, help="Título do lote (organizacional)")
@click.option("--tags", default=None, help="Tags separadas por vírgula")
@click.option("--native-schedule", is_flag=True, default=False, help="Forçar agendamento nativo imediato (para TikTok/Instagram)")
def create_batch(
    video_ids: tuple,
    render_ids: tuple,
    account_id: str,
    platform_str: str,
    caption: str,
    interval_minutes: int,
    start_at: Optional[str],
    title: Optional[str],
    tags: Optional[str],
    native_schedule: bool,
) -> None:
    """Cria um lote: um post por VIDEO_ID ou RENDER_ID, espaçados por --interval-minutes."""
    if not video_ids and not render_ids:
        console.print("[bold red]Informe ao menos um VIDEO_ID ou passe --render-id (-r).[/bold red]")
        sys.exit(1)

    db = Database()
    jq = JobQueue(db)
    scheduler = BatchScheduler(db, jq)
    platform = Platform(platform_str)
    parsed_start = datetime.fromisoformat(start_at) if start_at else None
    tag_list = [t.strip() for t in tags.split(",")] if tags else None

    try:
        created_batch = scheduler.create_batch(
            video_ids=list(video_ids) if video_ids else None,
            render_ids=list(render_ids) if render_ids else None,
            account_id=account_id,
            platform=platform,
            caption_template=caption,
            interval_seconds=interval_minutes * 60,
            start_at=parsed_start,
            title=title,
            tags=tag_list,
            native_schedule=native_schedule,
        )
    except ValueError as e:
        console.print(f"[bold red]Failed to create batch:[/bold red] {e}")
        sys.exit(1)

    total_items = len(video_ids) + len(render_ids)
    native = (platform in NATIVE_SCHEDULE_ON_UPLOAD) or native_schedule
    console.print(Panel(
        f"[bold green]Batch {created_batch.id} criado com {total_items} posts[/bold green]\n"
        f"Início: {created_batch.start_at.isoformat()} | Intervalo: {interval_minutes}min\n"
        + (
            "[cyan]YouTube: upload já enfileirado com publishAt nativo — a plataforma publica sozinha.[/cyan]"
            if native
            else f"[yellow]{platform.value}: sem agendamento nativo implementado — os posts ficam SCHEDULED e serão liberados por 'batch scan-due' no horário exato.[/yellow]"
        ),
        title="Batch criado",
    ))


@batch.command("list")
@click.option("--batch-id", default=None, help="Filtrar posts de um lote específico")
def list_batches(batch_id: Optional[str]) -> None:
    """Lista lotes existentes, ou os posts de um lote específico com --batch-id."""
    db = Database()

    if batch_id:
        posts = db.list_posts(batch_id=batch_id)
        table = Table(title=f"Posts do lote {batch_id}", header_style="bold magenta")
        table.add_column("ID", style="dim")
        table.add_column("Plataforma")
        table.add_column("Agendado para")
        table.add_column("Status", style="bold")
        table.add_column("Legenda")
        for p in posts:
            table.add_row(p.id, p.platform.value, p.scheduled_at.isoformat(), p.status.value, (p.caption or "")[:40])
        console.print(table)
        return

    batches = db.list_post_batches()
    if not batches:
        console.print("[yellow]Nenhum lote criado ainda.[/yellow]")
        return

    table = Table(title="Lotes de postagem", header_style="bold magenta")
    table.add_column("ID", style="dim")
    table.add_column("Título")
    table.add_column("Início")
    table.add_column("Intervalo")
    table.add_column("Status")
    for b in batches:
        table.add_row(b.id, b.title or "-", b.start_at.isoformat(), f"{b.interval_seconds // 60}min", b.status)
    console.print(table)


@batch.command("scan-due")
def scan_due() -> None:
    """
    Libera jobs de publish pra posts SCHEDULED cujo horário já chegou.
    Necessário só pra plataformas sem automação de agendamento nativo ainda
    (hoje: TikTok, Instagram) — rode periodicamente via cron/Agendador de
    Tarefas (ex: a cada minuto).
    """
    db = Database()
    jq = JobQueue(db)
    scanner = DueScanner(db, jq)
    count = scanner.run_once()
    console.print(f"[green]{count} job(s) de publish enfileirado(s).[/green]" if count else "[dim]Nenhum post vencido no momento.[/dim]")


# -----------------------------------------------------------------------------
# QUEUE & WORKER COMMANDS
# -----------------------------------------------------------------------------
@main.group()
def queue() -> None:
    """Inspect and manage the SQLite Job Queue."""
    pass


@queue.command("list")
@click.option("--status", help="Filter by job status (queued, running, completed, failed)")
@click.option("--type", "job_type", help="Filter by job type (download, publish, render)")
@click.option("--limit", default=30, help="Number of records to show")
def list_queue_jobs(status: Optional[str], job_type: Optional[str], limit: int) -> None:
    """Display pending and processed jobs in the queue."""
    db = Database()
    jq = JobQueue(db)

    j_status = JobStatus(status) if status and status in JobStatus._value2member_map_ else None
    j_type = JobType(job_type) if job_type and job_type in JobType._value2member_map_ else None

    jobs = jq.list_jobs(status=j_status, job_type=j_type, limit=limit)

    if not jobs:
        console.print("[yellow]No jobs found in queue matching filters.[/yellow]")
        return

    table = Table(title="Jobs Queue", header_style="bold yellow")
    table.add_column("ID", style="dim")
    table.add_column("Type", style="bold")
    table.add_column("Status")
    table.add_column("Attempts")
    table.add_column("Payload Summary")
    table.add_column("Error")
    table.add_column("Created At")

    status_colors = {
        JobStatus.QUEUED: "yellow",
        JobStatus.RUNNING: "cyan",
        JobStatus.COMPLETED: "green",
        JobStatus.FAILED: "red",
    }

    for j in jobs:
        color = status_colors.get(j.status, "white")
        status_str = f"[{color}]{j.status.value}[/{color}]"
        payload_summary = str(j.payload.get("source_url") or j.payload.get("video_path") or j.payload)
        if len(payload_summary) > 40:
            payload_summary = payload_summary[:37] + "..."

        table.add_row(
            j.id,
            j.type.value.upper(),
            status_str,
            f"{j.attempts}/{j.max_attempts}",
            payload_summary,
            f"[red]{j.error[:30]}...[/red]" if j.error else "[dim]-[/dim]",
            j.created_at.strftime("%Y-%m-%d %H:%M:%S") if j.created_at else "N/A",
        )

    console.print(table)


# -----------------------------------------------------------------------------
# WORKERS COMMAND
# -----------------------------------------------------------------------------
@main.group()
def workers() -> None:
    """Manage background worker daemons."""
    pass


@workers.command("start")
@click.option("--downloader-only", is_flag=True, help="Run only downloader worker")
@click.option("--publisher-only", is_flag=True, help="Run only publisher worker")
@click.option("--poll-interval", default=2.0, help="Queue polling interval in seconds")
def start_workers(downloader_only: bool, publisher_only: bool, poll_interval: float) -> None:
    """Start worker daemon processes to consume jobs from SQLite queue."""
    db = Database()
    queue = JobQueue(db)

    if downloader_only:
        console.print("[cyan]Starting Downloader Worker...[/cyan]")
        worker = DownloaderWorker(db, queue, poll_interval=poll_interval)
        worker.run()
    elif publisher_only:
        console.print("[cyan]Starting Publisher Worker...[/cyan]")
        worker = PublisherWorker(db, queue, poll_interval=poll_interval)
        worker.run()
    else:
        console.print(Panel(
            "[bold green]Starting All Clip Forge Workers[/bold green]\n"
            "• Downloader Worker (yt-dlp)\n"
            "• Publisher Worker (YouTube native publishAt)\n"
            f"Polling interval: {poll_interval}s\n"
            "[dim]Press Ctrl+C to stop workers cleanly.[/dim]",
            title="Worker Runner",
        ))
        runner = WorkerRunner(db, queue)
        runner.start_all(poll_interval=poll_interval)


# -----------------------------------------------------------------------------
# VIDEOS & POSTS LISTING
# -----------------------------------------------------------------------------
@main.group()
def videos() -> None:
    """Inspect downloaded and processed videos."""
    pass


@videos.command("list")
def list_videos() -> None:
    """List videos in local database."""
    db = Database()
    vids = db.list_videos()

    if not vids:
        console.print("[yellow]No videos found in database.[/yellow]")
        return

    table = Table(title="Local Videos Catalog", header_style="bold blue")
    table.add_column("ID", style="dim")
    table.add_column("Title")
    table.add_column("Kind", style="bold")
    table.add_column("Duration")
    table.add_column("Status")
    table.add_column("Local Path", style="dim")

    for v in vids:
        status_color = "green" if v.status == VideoStatus.DOWNLOADED else ("red" if v.status == VideoStatus.ERROR else "yellow")
        status_str = f"[{status_color}]{v.status.value}[/{status_color}]"
        dur = f"{int(v.duration_seconds)}s" if v.duration_seconds else "N/A"
        kind_badge_colors = {VideoKind.SHORT: "magenta", VideoKind.LONG: "cyan", VideoKind.LIVE_VOD: "red"}
        badge_color = kind_badge_colors.get(v.kind, "white")
        kind_badge = f"[{badge_color}]{v.kind.value.upper()}[/{badge_color}]"

        table.add_row(
            v.id,
            v.title or "Untitled",
            kind_badge,
            dur,
            status_str,
            v.local_path or "[dim]None[/dim]",
        )

    console.print(table)


# -----------------------------------------------------------------------------
# TEMPLATES COMMANDS (Canva Apps SDK)
# -----------------------------------------------------------------------------
@main.group()
def templates() -> None:
    """Manage Canva video templates and placeholder mapping."""
    pass


@templates.command("list")
def list_templates() -> None:
    """List all registered Canva templates and their mapping status."""
    db = Database()
    mgr = TemplateManager(db)
    tpls = mgr.list_templates()

    if not tpls:
        console.print("[yellow]No templates registered. Use 'clipforge templates add <url> --name <name>' to add one.[/yellow]")
        return

    table = Table(title="Canva Templates Catalog", header_style="bold magenta")
    table.add_column("ID", style="dim")
    table.add_column("Name", style="bold")
    table.add_column("Design ID")
    table.add_column("Status")
    table.add_column("URL", style="dim")

    for t in tpls:
        is_mapped = bool(t.placeholder_map)
        status_str = "[green]MAPPED[/green]" if is_mapped else "[yellow]UNMAPPED (Run map)[/yellow]"
        table.add_row(
            t.id,
            t.name,
            t.canva_design_id or "[dim]N/A[/dim]",
            status_str,
            t.template_url or "[dim]None[/dim]",
        )

    console.print(table)


@templates.command("add")
@click.argument("url")
@click.option("--name", required=True, help="Friendly name for the template")
def add_template(url: str, name: str) -> None:
    """Register a new Canva template URL."""
    db = Database()
    mgr = TemplateManager(db)
    try:
        tpl = mgr.add_template(name=name, template_url=url)
        console.print(f"[bold green]Successfully registered template '{tpl.name}' (ID: {tpl.id})[/bold green]")
        console.print(f"[dim]Next step: Run 'clipforge templates map {tpl.id}' to map the video placeholder.[/dim]")
    except Exception as e:
        console.print(f"[bold red]Failed to add template:[/bold red] {e}")


@templates.command("map")
@click.argument("template_id")
@click.option("--timeout", default=180, help="Interactive mapping timeout in seconds")
def map_template(template_id: str, timeout: int) -> None:
    """Open Canva and interactively map the video placeholder slot (1-click)."""
    db = Database()
    mapper = TemplateMapper(db)
    console.print(f"[cyan]Starting interactive mapping for template {template_id}...[/cyan]")
    try:
        updated = mapper.map_template(template_id=template_id, timeout_seconds=timeout)
        console.print(f"[bold green]Template '{updated.name}' mapped successfully![/bold green]")
    except Exception as e:
        console.print(f"[bold red]Mapping failed:[/bold red] {e}")


@templates.command("delete")
@click.argument("template_id")
def delete_template(template_id: str) -> None:
    """Delete a template from the catalog."""
    db = Database()
    mgr = TemplateManager(db)
    if mgr.delete_template(template_id):
        console.print(f"[green]Template {template_id} deleted successfully.[/green]")
    else:
        console.print(f"[yellow]Template {template_id} not found.[/yellow]")


# -----------------------------------------------------------------------------
# RENDER COMMANDS (Editor & Video Processing)
# -----------------------------------------------------------------------------
@main.group()
def render() -> None:
    """Manage video rendering with Canva templates."""
    pass


@render.command("enqueue")
@click.argument("video_id")
@click.option("--template-id", required=True, help="Template ID to combine with video")
def enqueue_render(video_id: str, template_id: str) -> None:
    """Enqueue a video render job (video + template -> final MP4)."""
    db = Database()
    queue = JobQueue(db)

    video = db.get_video(video_id)
    if not video:
        console.print(f"[bold red]Video not found:[/bold red] {video_id}")
        return

    template = db.get_template(template_id)
    if not template:
        console.print(f"[bold red]Template not found:[/bold red] {template_id}")
        return

    if not template.placeholder_map:
        console.print(f"[bold red]Template '{template.name}' has not been mapped yet. Run 'clipforge templates map {template_id}' first.[/bold red]")
        return

    render_id = f"render_{uuid.uuid4().hex[:12]}"
    job = queue.enqueue(
        job_type=JobType.RENDER,
        payload={
            "render_id": render_id,
            "video_id": video_id,
            "template_id": template_id,
        },
    )
    console.print(f"[bold green]Enqueued render job {job.id} (Render ID: {render_id})[/bold green]")
    console.print(f"[dim]Run 'clipforge workers start' to process the job.[/dim]")


@render.command("list")
@click.option("--limit", default=20, help="Maximum number of renders to list")
def list_renders(limit: int) -> None:
    """List recent video render jobs and their status."""
    db = Database()
    renders = db.list_renders(limit=limit)

    if not renders:
        console.print("[yellow]No render records found.[/yellow]")
        return

    table = Table(title="Render History", header_style="bold green")
    table.add_column("Render ID", style="dim")
    table.add_column("Video ID")
    table.add_column("Template ID")
    table.add_column("Status", style="bold")
    table.add_column("Output File", style="dim")

    for r in renders:
        status_color = "green" if r.status == RenderStatus.COMPLETED else ("red" if r.status == RenderStatus.FAILED else "yellow")
        status_str = f"[{status_color}]{r.status.value.upper()}[/{status_color}]"
        table.add_row(
            r.id,
            r.video_id,
            r.template_id or "N/A",
            status_str,
            r.output_path or "[dim]Pending[/dim]",
        )

    console.print(table)


@main.command("ui")
@click.option("--browser", is_flag=True, help="Open UI in default web browser instead of desktop window")
def launch_ui(browser: bool) -> None:
    """Launch the Clip Forge V2 Desktop UI (Flet)."""
    from clipforge.ui.app import start_app
    console.print("[bold green]Starting Clip Forge V2 Desktop UI...[/bold green]")
    start_app(browser=browser)


if __name__ == "__main__":
    main()

