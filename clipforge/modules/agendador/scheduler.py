"""
Agendador — criação de lotes de postagem (planejamento seção 3.5).

Decisões implementadas aqui:
- Postagem em massa: seleciona N vídeos, aplica a mesma legenda (com
  possibilidade de placeholder `{n}` pro número sequencial do post) e um
  intervalo entre eles, separado por plataforma.
- Agendamento nativo vs. disparado: pra YouTube, o job de publish é
  enfileirado IMEDIATAMENTE — o upload acontece agora, com `publishAt` no
  futuro (é o YouTube quem guarda e publica sozinho depois). Pra qualquer
  outra plataforma, o post fica com status SCHEDULED e não gera job ainda;
  quem libera o job na hora certa é o `DueScanner` (ver due_scanner.py) —
  hoje isso serve de fallback pro TikTok e Instagram, até a automação do
  agendador nativo deles (TikTok Studio / Meta Business Suite) ser
  implementada (ver seção 3.5 do planejamento).
"""

from __future__ import annotations
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from clipforge.core.db import Database
from clipforge.core.models import JobType, Platform, Post, PostBatch, PostStatus, VideoStatus
from clipforge.core.queue import JobQueue

# Plataformas cujo "publish" já garante agendamento nativo no momento do
# upload (não precisam do job disparado exatamente na hora agendada).
NATIVE_SCHEDULE_ON_UPLOAD = {Platform.YOUTUBE}


class BatchScheduler:
    def __init__(self, db: Database, queue: JobQueue):
        self.db = db
        self.queue = queue

    def create_batch(
        self,
        video_ids: List[str],
        account_id: str,
        platform: Platform,
        caption_template: str,
        interval_seconds: int = 3600,
        start_at: Optional[datetime] = None,
        title: Optional[str] = None,
        tags: Optional[List[str]] = None,
        native_schedule: bool = False,
    ) -> PostBatch:
        """
        Cria um PostBatch + um Post por vídeo, com `scheduled_at` calculado
        como `start_at + i * interval_seconds`. Pra YouTube, já enfileira o
        job de publish (upload com publishAt nativo); pras demais, deixa
        SCHEDULED pro DueScanner.

        `caption_template` pode conter `{n}` (número sequencial, 1-based)
        e `{total}` (total de posts do lote) — ex: "Parte {n}/{total} 🔥".
        """
        if not video_ids:
            raise ValueError("create_batch requires at least one video_id")

        start_at = start_at or datetime.now(timezone.utc)
        batch = PostBatch(
            id=f"batch_{uuid.uuid4().hex[:12]}",
            title=title,
            caption_template=caption_template,
            interval_seconds=interval_seconds,
            start_at=start_at,
            status="scheduled",
        )
        self.db.save_post_batch(batch)

        total = len(video_ids)
        for i, video_id in enumerate(video_ids):
            video = self.db.get_video(video_id)
            if not video:
                raise ValueError(f"Video not found: {video_id}")
            if video.status != VideoStatus.DOWNLOADED or not video.local_path:
                raise ValueError(f"Video {video_id} is not downloaded yet (status={video.status})")

            scheduled_at = start_at + timedelta(seconds=interval_seconds * i)
            caption = caption_template.format(n=i + 1, total=total)

            post = Post(
                id=f"post_{uuid.uuid4().hex[:12]}",
                batch_id=batch.id,
                video_id=video_id,
                account_id=account_id,
                platform=platform,
                title=video.title,
                caption=caption,
                tags=tags or [],
                scheduled_at=scheduled_at,
                status=PostStatus.SCHEDULED,
            )
            self.db.save_post(post)

            if platform in NATIVE_SCHEDULE_ON_UPLOAD or native_schedule:
                # Sobe agora, com agendamento nativo no futuro — a própria plataforma
                # publica sozinha depois, sem precisar do nosso sistema
                # rodando na hora exata (ver planejamento seção 3.5).
                self.queue.enqueue(
                    job_type=JobType.PUBLISH,
                    payload={
                        "post_id": post.id,
                        "account_id": account_id,
                        "platform": platform.value,
                        "video_path": video.local_path,
                        "title": post.title,
                        "description": post.caption,
                        "tags": post.tags,
                        "publish_at": scheduled_at.isoformat(),
                    },
                )
            # Demais plataformas: fica SCHEDULED, sem job ainda — o
            # DueScanner libera o job quando `scheduled_at` chegar.

        return batch
