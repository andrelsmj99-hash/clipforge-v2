"""
DueScanner — libera jobs de publish na hora exata, pra plataformas sem
agendamento nativo já automatizado (hoje: TikTok e Instagram, até a
automação do agendador nativo deles — TikTok Studio / Meta Business Suite —
ser implementada. Ver planejamento seção 3.5/3.6).

Diferente do Downloader/Publisher (que ficam num loop contínuo puxando da
fila), o DueScanner é pensado pra rodar periodicamente (ex: a cada minuto,
via `workers start` ou um cron externo) — ele só precisa checar "algum post
venceu?", não ficar bloqueado esperando.
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Optional

from clipforge.core.db import Database
from clipforge.core.models import JobType, PostStatus
from clipforge.core.queue import JobQueue

logger = logging.getLogger(__name__)


class DueScanner:
    def __init__(self, db: Database, queue: JobQueue):
        self.db = db
        self.queue = queue

    def run_once(self, now: Optional[datetime] = None) -> int:
        """
        Verifica posts SCHEDULED cujo horário já chegou e enfileira o job de
        publish correspondente. Retorna quantos jobs foram enfileirados.

        Usa `claim_due_post` (UPDATE atômico condicionado a status='scheduled')
        antes de enfileirar — se duas chamadas rodarem ao mesmo tempo (ex:
        scanner + reinício manual), só uma consegue reivindicar cada post,
        evitando publicar em duplicidade.
        """
        now = now or datetime.now(timezone.utc)
        due_posts = self.db.list_due_posts(now=now)
        enqueued = 0

        for post in due_posts:
            if not self.db.claim_due_post(post.id):
                continue  # outra chamada já reivindicou este post

            video_path = None
            if post.video_id:
                video = self.db.get_video(post.video_id)
                video_path = video.local_path if video else None
            elif post.render_id:
                render = self.db.get_render(post.render_id)
                video_path = render.output_path if render else None

            if not video_path:
                post.status = PostStatus.FAILED
                post.error_message = "Nenhum arquivo de vídeo associado ao post (video_id/render_id ausente ou sem output)."
                self.db.save_post(post)
                logger.error(f"Post {post.id} sem video_path resolvível — marcado como FAILED.")
                continue

            self.queue.enqueue(
                job_type=JobType.PUBLISH,
                payload={
                    "post_id": post.id,
                    "account_id": post.account_id,
                    "platform": post.platform.value,
                    "video_path": video_path,
                    "title": post.title,
                    "description": post.caption,
                    "tags": post.tags,
                },
            )
            enqueued += 1
            logger.info(f"Post {post.id} venceu — job de publish enfileirado ({post.platform.value}).")

        return enqueued
