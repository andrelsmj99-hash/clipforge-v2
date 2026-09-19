"""Core data models and schemas using Pydantic."""

from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator


class Platform(str, Enum):
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"


class AccountStatus(str, Enum):
    CONNECTED = "connected"
    EXPIRED = "expired"
    DISCONNECTED = "disconnected"


class VideoKind(str, Enum):
    SHORT = "short"
    LONG = "long"
    LIVE_VOD = "live_vod"
    UNKNOWN = "unknown"


class VideoStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADED = "downloaded"
    ERROR = "error"


class RenderStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class PostStatus(str, Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    UPLOADING = "uploading"
    PUBLISHED = "published"
    FAILED = "failed"


class JobType(str, Enum):
    DOWNLOAD = "download"
    RENDER = "render"
    PUBLISH = "publish"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# --- Database Entity Models ---

class Account(BaseModel):
    id: str
    platform: Platform
    name: Optional[str] = None
    status: AccountStatus = AccountStatus.DISCONNECTED
    credentials_json: Optional[str] = None
    token_expires_at: Optional[datetime] = None
    session_cookies: Optional[str] = None
    connected_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class Channel(BaseModel):
    id: str
    account_id: Optional[str] = None
    platform: Platform
    external_id: str
    name: Optional[str] = None
    url: str
    created_at: Optional[datetime] = None


class Video(BaseModel):
    id: str
    channel_id: Optional[str] = None
    source_url: str
    title: Optional[str] = None
    local_path: Optional[str] = None
    kind: VideoKind = VideoKind.UNKNOWN
    duration_seconds: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    status: VideoStatus = VideoStatus.PENDING
    error_message: Optional[str] = None
    downloaded_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class Template(BaseModel):
    id: str
    canva_design_id: Optional[str] = None
    name: str
    template_url: Optional[str] = None
    placeholder_map: Optional[Dict[str, Any]] = None
    mapped_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    @model_validator(mode="before")
    @classmethod
    def _sync_canva_ids(cls, data: Any) -> Any:
        if isinstance(data, dict):
            design_id = data.get("canva_design_id") or data.get("canva_template_id")
            if design_id:
                data["canva_design_id"] = design_id
        return data

    @property
    def canva_template_id(self) -> Optional[str]:
        """Backward-compatible property for canva_design_id."""
        return self.canva_design_id

    @canva_template_id.setter
    def canva_template_id(self, val: Optional[str]) -> None:
        self.canva_design_id = val


class Render(BaseModel):
    id: str
    video_id: str
    template_id: Optional[str] = None
    output_path: Optional[str] = None
    status: RenderStatus = RenderStatus.PENDING
    created_at: Optional[datetime] = None


class PostBatch(BaseModel):
    id: str
    title: Optional[str] = None
    caption_template: Optional[str] = None
    interval_seconds: int = 3600
    start_at: datetime
    status: str = "scheduled"
    created_at: Optional[datetime] = None


class Post(BaseModel):
    id: str
    batch_id: Optional[str] = None
    render_id: Optional[str] = None
    video_id: Optional[str] = None
    account_id: str
    platform: Platform = Platform.YOUTUBE
    title: Optional[str] = None
    caption: Optional[str] = None
    tags: Optional[List[str]] = Field(default_factory=list)
    scheduled_at: datetime
    status: PostStatus = PostStatus.DRAFT
    external_post_id: Optional[str] = None
    error_message: Optional[str] = None
    published_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class Job(BaseModel):
    id: str
    type: JobType
    payload: Dict[str, Any]
    status: JobStatus = JobStatus.QUEUED
    attempts: int = 0
    max_attempts: int = 3
    error: Optional[str] = None
    locked_by: Optional[str] = None
    locked_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# --- Job Payloads ---

class DownloadJobPayload(BaseModel):
    video_id: str
    source_url: str
    channel_id: Optional[str] = None
    platform: Platform = Platform.YOUTUBE
    title: Optional[str] = None
    kind: VideoKind = VideoKind.UNKNOWN
    use_cookies: bool = True
    account_id: Optional[str] = None


class PublishJobPayload(BaseModel):
    post_id: str
    account_id: str
    platform: Platform
    video_path: str
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = Field(default_factory=list)
    publish_at: Optional[datetime] = None
    privacy_status: str = "private"
    made_for_kids: bool = False
