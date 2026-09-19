"""SQLite Database manager, schema initialization, and transactional operations."""

from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

from clipforge.core.config import DATABASE_PATH
from clipforge.core.models import (
    Account,
    AccountStatus,
    Channel,
    Job,
    JobStatus,
    JobType,
    Platform,
    Post,
    PostBatch,
    PostStatus,
    Render,
    RenderStatus,
    Template,
    Video,
    VideoKind,
    VideoStatus,
)

SCHEMA_SQL = """
-- Accounts & Sessions
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    name TEXT,
    status TEXT NOT NULL DEFAULT 'disconnected',
    credentials_json TEXT,
    token_expires_at TEXT,
    session_cookies TEXT,
    connected_at TEXT,
    updated_at TEXT
);

-- Channels
CREATE TABLE IF NOT EXISTS channels (
    id TEXT PRIMARY KEY,
    account_id TEXT,
    platform TEXT NOT NULL,
    external_id TEXT NOT NULL,
    name TEXT,
    url TEXT NOT NULL,
    created_at TEXT,
    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE SET NULL
);

-- Downloaded Videos (with deduplication on source_url)
CREATE TABLE IF NOT EXISTS videos (
    id TEXT PRIMARY KEY,
    channel_id TEXT,
    source_url TEXT UNIQUE NOT NULL,
    title TEXT,
    local_path TEXT,
    kind TEXT CHECK(kind IN ('short', 'long', 'live_vod', 'unknown')) DEFAULT 'unknown',
    duration_seconds REAL,
    width INTEGER,
    height INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT,
    downloaded_at TEXT,
    created_at TEXT,
    FOREIGN KEY(channel_id) REFERENCES channels(id) ON DELETE SET NULL
);

-- Templates (Canva)
CREATE TABLE IF NOT EXISTS templates (
    id TEXT PRIMARY KEY,
    canva_template_id TEXT,
    canva_design_id TEXT,
    name TEXT NOT NULL,
    template_url TEXT,
    placeholder_map TEXT,
    mapped_at TEXT,
    created_at TEXT
);

-- Renders
CREATE TABLE IF NOT EXISTS renders (
    id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL,
    template_id TEXT,
    output_path TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT,
    FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
    FOREIGN KEY(template_id) REFERENCES templates(id) ON DELETE SET NULL
);

-- Post Batches
CREATE TABLE IF NOT EXISTS post_batches (
    id TEXT PRIMARY KEY,
    title TEXT,
    caption_template TEXT,
    interval_seconds INTEGER NOT NULL DEFAULT 3600,
    start_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled',
    created_at TEXT
);

-- Posts
CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY,
    batch_id TEXT,
    render_id TEXT,
    video_id TEXT,
    account_id TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT 'youtube',
    title TEXT,
    caption TEXT,
    tags TEXT,
    scheduled_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    external_post_id TEXT,
    error_message TEXT,
    published_at TEXT,
    created_at TEXT,
    FOREIGN KEY(batch_id) REFERENCES post_batches(id) ON DELETE SET NULL,
    FOREIGN KEY(render_id) REFERENCES renders(id) ON DELETE SET NULL,
    FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE SET NULL,
    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
);

-- Central Jobs Queue
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    error TEXT,
    locked_by TEXT,
    locked_at TEXT,
    created_at TEXT,
    updated_at TEXT
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_videos_source_url ON videos(source_url);
CREATE INDEX IF NOT EXISTS idx_videos_channel_id ON videos(channel_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status_type ON jobs(status, type);
CREATE INDEX IF NOT EXISTS idx_posts_scheduled ON posts(status, scheduled_at);
"""


def _now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 string format."""
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(iso_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO string back to datetime object."""
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str)
    except Exception:
        return None


class Database:
    def __init__(self, db_path: Path | str = DATABASE_PATH):
        self.db_path = Path(db_path) if isinstance(db_path, str) else db_path
        if self.db_path != Path(":memory:"):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    def get_connection(self) -> sqlite3.Connection:
        """Create a configured SQLite connection."""
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            check_same_thread=False,
            isolation_level=None,  # Autocommit mode, we handle transactions explicitly
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Cursor, None, None]:
        """Context manager for explicit transaction with automatic commit/rollback."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("BEGIN IMMEDIATE;")
        try:
            yield cursor
            cursor.execute("COMMIT;")
        except Exception:
            cursor.execute("ROLLBACK;")
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        """Execute table creation scripts."""
        with self.get_connection() as conn:
            conn.executescript(SCHEMA_SQL)
            # Ensure templates table has the new columns if migrated from earlier schema
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(templates)").fetchall()]
            if "canva_design_id" not in cols:
                conn.execute("ALTER TABLE templates ADD COLUMN canva_design_id TEXT")
            if "template_url" not in cols:
                conn.execute("ALTER TABLE templates ADD COLUMN template_url TEXT")
            if "mapped_at" not in cols:
                conn.execute("ALTER TABLE templates ADD COLUMN mapped_at TEXT")

    # --------------------------------------------------------------------------
    # Accounts
    # --------------------------------------------------------------------------
    def save_account(self, account: Account) -> None:
        now = _now_iso()
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO accounts (id, platform, name, status, credentials_json, token_expires_at, session_cookies, connected_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    platform = excluded.platform,
                    name = excluded.name,
                    status = excluded.status,
                    credentials_json = excluded.credentials_json,
                    token_expires_at = excluded.token_expires_at,
                    session_cookies = excluded.session_cookies,
                    updated_at = excluded.updated_at
                """,
                (
                    account.id,
                    account.platform.value if isinstance(account.platform, Platform) else account.platform,
                    account.name,
                    account.status.value if isinstance(account.status, AccountStatus) else account.status,
                    account.credentials_json,
                    account.token_expires_at.isoformat() if account.token_expires_at else None,
                    account.session_cookies,
                    account.connected_at.isoformat() if account.connected_at else now,
                    now,
                ),
            )

    def get_account(self, account_id: str) -> Optional[Account]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
            if not row:
                return None
            return Account(
                id=row["id"],
                platform=Platform(row["platform"]),
                name=row["name"],
                status=AccountStatus(row["status"]),
                credentials_json=row["credentials_json"],
                token_expires_at=_parse_iso(row["token_expires_at"]),
                session_cookies=row["session_cookies"],
                connected_at=_parse_iso(row["connected_at"]),
                updated_at=_parse_iso(row["updated_at"]),
            )

    def list_accounts(self, platform: Optional[Platform] = None) -> List[Account]:
        with self.get_connection() as conn:
            if platform:
                rows = conn.execute(
                    "SELECT * FROM accounts WHERE platform = ? ORDER BY connected_at DESC",
                    (platform.value if isinstance(platform, Platform) else platform,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM accounts ORDER BY connected_at DESC").fetchall()

            return [
                Account(
                    id=row["id"],
                    platform=Platform(row["platform"]),
                    name=row["name"],
                    status=AccountStatus(row["status"]),
                    credentials_json=row["credentials_json"],
                    token_expires_at=_parse_iso(row["token_expires_at"]),
                    session_cookies=row["session_cookies"],
                    connected_at=_parse_iso(row["connected_at"]),
                    updated_at=_parse_iso(row["updated_at"]),
                )
                for row in rows
            ]

    # --------------------------------------------------------------------------
    # Channels
    # --------------------------------------------------------------------------
    def save_channel(self, channel: Channel) -> None:
        now = _now_iso()
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO channels (id, account_id, platform, external_id, name, url, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    account_id = excluded.account_id,
                    platform = excluded.platform,
                    external_id = excluded.external_id,
                    name = excluded.name,
                    url = excluded.url
                """,
                (
                    channel.id,
                    channel.account_id,
                    channel.platform.value if isinstance(channel.platform, Platform) else channel.platform,
                    channel.external_id,
                    channel.name,
                    channel.url,
                    channel.created_at.isoformat() if channel.created_at else now,
                ),
            )

    def get_channel(self, channel_id: str) -> Optional[Channel]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
            if not row:
                return None
            return Channel(
                id=row["id"],
                account_id=row["account_id"],
                platform=Platform(row["platform"]),
                external_id=row["external_id"],
                name=row["name"],
                url=row["url"],
                created_at=_parse_iso(row["created_at"]),
            )

    def get_channel_by_url_or_external_id(self, identifier: str) -> Optional[Channel]:
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM channels WHERE url = ? OR external_id = ? LIMIT 1",
                (identifier, identifier),
            ).fetchone()
            if not row:
                return None
            return Channel(
                id=row["id"],
                account_id=row["account_id"],
                platform=Platform(row["platform"]),
                external_id=row["external_id"],
                name=row["name"],
                url=row["url"],
                created_at=_parse_iso(row["created_at"]),
            )

    # --------------------------------------------------------------------------
    # Videos (Deduplication & Management)
    # --------------------------------------------------------------------------
    def save_video(self, video: Video) -> None:
        now = _now_iso()
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO videos (
                    id, channel_id, source_url, title, local_path, kind,
                    duration_seconds, width, height, status, error_message,
                    downloaded_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    source_url = excluded.source_url,
                    title = excluded.title,
                    local_path = excluded.local_path,
                    kind = excluded.kind,
                    duration_seconds = excluded.duration_seconds,
                    width = excluded.width,
                    height = excluded.height,
                    status = excluded.status,
                    error_message = excluded.error_message,
                    downloaded_at = excluded.downloaded_at
                """,
                (
                    video.id,
                    video.channel_id,
                    video.source_url,
                    video.title,
                    video.local_path,
                    video.kind.value if isinstance(video.kind, VideoKind) else video.kind,
                    video.duration_seconds,
                    video.width,
                    video.height,
                    video.status.value if isinstance(video.status, VideoStatus) else video.status,
                    video.error_message,
                    video.downloaded_at.isoformat() if video.downloaded_at else None,
                    video.created_at.isoformat() if video.created_at else now,
                ),
            )

    def get_video_by_source_url(self, source_url: str) -> Optional[Video]:
        """Used for deduplication: checks if a video with the same source_url already exists."""
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM videos WHERE source_url = ?", (source_url,)).fetchone()
            if not row:
                return None
            return self._row_to_video(row)

    def get_video(self, video_id: str) -> Optional[Video]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
            if not row:
                return None
            return self._row_to_video(row)

    def list_videos(self, channel_id: Optional[str] = None, kind: Optional[VideoKind] = None) -> List[Video]:
        query = "SELECT * FROM videos WHERE 1=1"
        params: List[Any] = []
        if channel_id:
            query += " AND channel_id = ?"
            params.append(channel_id)
        if kind:
            query += " AND kind = ?"
            params.append(kind.value if isinstance(kind, VideoKind) else kind)
        query += " ORDER BY created_at DESC"

        with self.get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_video(r) for r in rows]

    def _row_to_video(self, row: sqlite3.Row) -> Video:
        return Video(
            id=row["id"],
            channel_id=row["channel_id"],
            source_url=row["source_url"],
            title=row["title"],
            local_path=row["local_path"],
            kind=VideoKind(row["kind"]) if row["kind"] in VideoKind._value2member_map_ else VideoKind.UNKNOWN,
            duration_seconds=row["duration_seconds"],
            width=row["width"],
            height=row["height"],
            status=VideoStatus(row["status"]) if row["status"] in VideoStatus._value2member_map_ else VideoStatus.PENDING,
            error_message=row["error_message"],
            downloaded_at=_parse_iso(row["downloaded_at"]),
            created_at=_parse_iso(row["created_at"]),
        )

    # --------------------------------------------------------------------------
    # Posts & Batches
    # --------------------------------------------------------------------------
    def save_post(self, post: Post) -> None:
        now = _now_iso()
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO posts (
                    id, batch_id, render_id, video_id, account_id, platform,
                    title, caption, tags, scheduled_at, status,
                    external_post_id, error_message, published_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    batch_id = excluded.batch_id,
                    render_id = excluded.render_id,
                    video_id = excluded.video_id,
                    account_id = excluded.account_id,
                    platform = excluded.platform,
                    title = excluded.title,
                    caption = excluded.caption,
                    tags = excluded.tags,
                    scheduled_at = excluded.scheduled_at,
                    status = excluded.status,
                    external_post_id = excluded.external_post_id,
                    error_message = excluded.error_message,
                    published_at = excluded.published_at
                """,
                (
                    post.id,
                    post.batch_id,
                    post.render_id,
                    post.video_id,
                    post.account_id,
                    post.platform.value if isinstance(post.platform, Platform) else post.platform,
                    post.title,
                    post.caption,
                    json.dumps(post.tags) if post.tags else "[]",
                    post.scheduled_at.isoformat(),
                    post.status.value if isinstance(post.status, PostStatus) else post.status,
                    post.external_post_id,
                    post.error_message,
                    post.published_at.isoformat() if post.published_at else None,
                    post.created_at.isoformat() if post.created_at else now,
                ),
            )

    def _row_to_post(self, row: sqlite3.Row) -> Post:
        return Post(
            id=row["id"],
            batch_id=row["batch_id"],
            render_id=row["render_id"],
            video_id=row["video_id"],
            account_id=row["account_id"],
            platform=Platform(row["platform"]) if row["platform"] in Platform._value2member_map_ else Platform.YOUTUBE,
            title=row["title"],
            caption=row["caption"],
            tags=json.loads(row["tags"]) if row["tags"] else [],
            scheduled_at=_parse_iso(row["scheduled_at"]) or datetime.now(timezone.utc),
            status=PostStatus(row["status"]) if row["status"] in PostStatus._value2member_map_ else PostStatus.DRAFT,
            external_post_id=row["external_post_id"],
            error_message=row["error_message"],
            published_at=_parse_iso(row["published_at"]),
            created_at=_parse_iso(row["created_at"]),
        )

    def get_post(self, post_id: str) -> Optional[Post]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
            if not row:
                return None
            return self._row_to_post(row)

    def list_posts(self, account_id: Optional[str] = None, status: Optional[PostStatus] = None, batch_id: Optional[str] = None) -> List[Post]:
        query = "SELECT * FROM posts WHERE 1=1"
        params: List[Any] = []
        if account_id:
            query += " AND account_id = ?"
            params.append(account_id)
        if status:
            query += " AND status = ?"
            params.append(status.value if isinstance(status, PostStatus) else status)
        if batch_id:
            query += " AND batch_id = ?"
            params.append(batch_id)
        query += " ORDER BY scheduled_at ASC"

        with self.get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_post(r) for r in rows]

    def claim_due_post(self, post_id: str) -> bool:
        """
        Atomically claim a SCHEDULED post whose time has come, flipping it to
        UPLOADING so the due-scanner never enqueues the same post twice
        (mirrors the atomic claim pattern used by JobQueue.acquire_next).
        Returns True if this call won the claim.
        """
        with self.transaction() as cur:
            cur.execute(
                "UPDATE posts SET status = 'uploading' WHERE id = ? AND status = 'scheduled'",
                (post_id,),
            )
            return cur.rowcount > 0

    def list_due_posts(self, now: Optional[datetime] = None) -> List[Post]:
        """Posts that are SCHEDULED and whose scheduled_at has already passed."""
        now = now or datetime.now(timezone.utc)
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM posts WHERE status = 'scheduled' AND scheduled_at <= ? ORDER BY scheduled_at ASC",
                (now.isoformat(),),
            ).fetchall()
            return [self._row_to_post(r) for r in rows]

    # --------------------------------------------------------------------------
    # Post Batches
    # --------------------------------------------------------------------------
    def save_post_batch(self, batch: PostBatch) -> None:
        now = _now_iso()
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO post_batches (id, title, caption_template, interval_seconds, start_at, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    caption_template = excluded.caption_template,
                    interval_seconds = excluded.interval_seconds,
                    start_at = excluded.start_at,
                    status = excluded.status
                """,
                (
                    batch.id,
                    batch.title,
                    batch.caption_template,
                    batch.interval_seconds,
                    batch.start_at.isoformat(),
                    batch.status,
                    batch.created_at.isoformat() if batch.created_at else now,
                ),
            )

    def get_post_batch(self, batch_id: str) -> Optional[PostBatch]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM post_batches WHERE id = ?", (batch_id,)).fetchone()
            if not row:
                return None
            return PostBatch(
                id=row["id"],
                title=row["title"],
                caption_template=row["caption_template"],
                interval_seconds=row["interval_seconds"],
                start_at=_parse_iso(row["start_at"]) or datetime.now(timezone.utc),
                status=row["status"],
                created_at=_parse_iso(row["created_at"]),
            )

    def list_post_batches(self) -> List[PostBatch]:
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM post_batches ORDER BY created_at DESC").fetchall()
            return [
                PostBatch(
                    id=r["id"],
                    title=r["title"],
                    caption_template=r["caption_template"],
                    interval_seconds=r["interval_seconds"],
                    start_at=_parse_iso(r["start_at"]) or datetime.now(timezone.utc),
                    status=r["status"],
                    created_at=_parse_iso(r["created_at"]),
                )
                for r in rows
            ]

    # --------------------------------------------------------------------------
    # Templates & Renders (Editor module — planejamento seção 9, ainda não implementado)
    # --------------------------------------------------------------------------
    def save_template(self, template: Template) -> None:
        now = _now_iso()
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO templates (id, canva_template_id, canva_design_id, name, template_url, placeholder_map, mapped_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    canva_template_id = excluded.canva_template_id,
                    canva_design_id = excluded.canva_design_id,
                    name = excluded.name,
                    template_url = excluded.template_url,
                    placeholder_map = excluded.placeholder_map,
                    mapped_at = excluded.mapped_at
                """,
                (
                    template.id,
                    template.canva_template_id,
                    template.canva_design_id,
                    template.name,
                    template.template_url,
                    json.dumps(template.placeholder_map) if template.placeholder_map else None,
                    template.mapped_at.isoformat() if template.mapped_at else None,
                    template.created_at.isoformat() if template.created_at else now,
                ),
            )

    def _row_to_template(self, row: sqlite3.Row) -> Template:
        cols = row.keys()
        design_id = (row["canva_design_id"] if "canva_design_id" in cols and row["canva_design_id"] else None) or (
            row["canva_template_id"] if "canva_template_id" in cols and row["canva_template_id"] else None
        )
        return Template(
            id=row["id"],
            canva_design_id=design_id,
            name=row["name"],
            template_url=row["template_url"] if "template_url" in cols else None,
            placeholder_map=json.loads(row["placeholder_map"]) if row["placeholder_map"] else None,
            mapped_at=_parse_iso(row["mapped_at"]) if "mapped_at" in cols and row["mapped_at"] else None,
            created_at=_parse_iso(row["created_at"]),
        )

    def get_template(self, template_id: str) -> Optional[Template]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,)).fetchone()
            if not row:
                return None
            return self._row_to_template(row)

    def get_template_by_url(self, template_url: str) -> Optional[Template]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM templates WHERE template_url = ? LIMIT 1", (template_url,)).fetchone()
            if not row:
                return None
            return self._row_to_template(row)

    def list_templates(self) -> List[Template]:
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM templates ORDER BY created_at DESC").fetchall()
            return [self._row_to_template(r) for r in rows]

    def delete_template(self, template_id: str) -> bool:
        with self.transaction() as cur:
            cur.execute("DELETE FROM templates WHERE id = ?", (template_id,))
            return cur.rowcount > 0

    def save_render(self, render: Render) -> None:
        now = _now_iso()
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO renders (id, video_id, template_id, output_path, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    video_id = excluded.video_id,
                    template_id = excluded.template_id,
                    output_path = excluded.output_path,
                    status = excluded.status
                """,
                (
                    render.id,
                    render.video_id,
                    render.template_id,
                    render.output_path,
                    render.status.value if isinstance(render.status, RenderStatus) else render.status,
                    render.created_at.isoformat() if render.created_at else now,
                ),
            )

    def _row_to_render(self, row: sqlite3.Row) -> Render:
        return Render(
            id=row["id"],
            video_id=row["video_id"],
            template_id=row["template_id"],
            output_path=row["output_path"],
            status=RenderStatus(row["status"]) if row["status"] in RenderStatus._value2member_map_ else RenderStatus.PENDING,
            created_at=_parse_iso(row["created_at"]),
        )

    def get_render(self, render_id: str) -> Optional[Render]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM renders WHERE id = ?", (render_id,)).fetchone()
            if not row:
                return None
            return self._row_to_render(row)

    def list_renders(self, limit: int = 50) -> List[Render]:
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM renders ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            return [self._row_to_render(r) for r in rows]
