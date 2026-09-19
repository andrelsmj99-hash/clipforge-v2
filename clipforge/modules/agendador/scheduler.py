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
        video_ids: Optional[List[str]] = None,
        account_id: str = "",
        platform: Platform = Platform.YOUTUBE,
        caption_template: str = "",
        interval_seconds: int = 3600,
        start_at: Optional[datetime] = None,
        title: Optional[str] = None,
        tags: Optional[List[str]] = None,
        native_schedule: bool = False,
        render_ids: Optional[List[str]] = None,
    ) -> PostBatch:
        """
        Cria um PostBatch + um Post por vídeo/render, com `scheduled_at` calculado
        como `start_at + i * interval_seconds`. Pra YouTube, já enfileira o
        job de publish (upload com publishAt nativo); pras demais, deixa
        SCHEDULED pro DueScanner.

        Aceita tanto `video_ids` (vídeos baixados brutos) quanto `render_ids`
        (vídeos finalizados pelo editor/templates).

        `caption_template` pode conter `{n}` (número sequencial, 1-based)
        e `{total}` (total de posts do lote) — ex: "Parte {n}/{total} 🔥".
        """
        items: List[tuple[str, str]] = []
        if video_ids:
            for vid in video_ids:
                items.append(("video", vid))
        if render_ids:
            for rid in render_ids:
                items.append(("render", rid))

        if not items:
            raise ValueError("create_batch requires at least one video_id or render_id")

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

        total = len(items)
        for i, (item_type, item_id) in enumerate(items):
            if item_type == "video":
                video = self.db.get_video(item_id)
                if not video:
                    raise ValueError(f"Video not found: {item_id}")
                if video.status != VideoStatus.DOWNLOADED or not video.local_path:
                    raise ValueError(f"Video {item_id} is not downloaded yet (status={video.status})")

                post_title = title or video.title
                video_path = video.local_path
                post_video_id: Optional[str] = item_id
                post_render_id: Optional[str] = None
            else:
                from clipforge.core.models import RenderStatus
                render = self.db.get_render(item_id)
                if not render:
                    raise ValueError(f"Render not found: {item_id}")
                if render.status != RenderStatus.COMPLETED or not render.output_path:
                    raise ValueError(f"Render {item_id} is not completed yet (status={render.status})")

                v_title = None
                if render.video_id:
                    v = self.db.get_video(render.video_id)
                    if v:
                        v_title = v.title

                post_title = title or v_title or f"Render {render.id}"
                video_path = render.output_path
                post_video_id = None
                post_render_id = item_id

            scheduled_at = start_at + timedelta(seconds=interval_seconds * i)
            caption = caption_template.format(n=i + 1, total=total)

            post = Post(
                id=f"post_{uuid.uuid4().hex[:12]}",
                batch_id=batch.id,
                video_id=post_video_id,
                render_id=post_render_id,
                account_id=account_id,
                platform=platform,
                title=post_title,
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
                        "video_path": video_path,
                        "title": post.title,
                        "description": post.caption,
                        "tags": post.tags,
                        "publish_at": scheduled_at.isoformat(),
                    },
                )
            # Demais plataformas: fica SCHEDULED, sem job ainda — o
            # DueScanner libera o job quando `scheduled_at` chegar.

        return batch
