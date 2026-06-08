from __future__ import annotations

from ..config import AppConfig
from ..db import Repository
from ..logging_utils import log
from ..quotas import QuotaService
from .enrichment import EnrichmentService
from .outreach import OutreachService
from .queue import QueueService


class SchedulerService:
    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo

    def run_once(self, enrich_limit: int, queue_limit: int, send_limit: int) -> None:
        with self.repo.db.connect() as conn:
            row = conn.execute("SELECT pg_try_advisory_lock(20260603) AS locked").fetchone()
            if not row["locked"]:
                log("scheduler.skipped_locked")
                return
            try:
                quota = QuotaService(self.config, self.repo)
                EnrichmentService(self.config, self.repo).enrich(enrich_limit)
                QueueService(self.repo).queue(queue_limit)
                limited_send = min(send_limit, quota.remaining_global("send"))
                sent = OutreachService(self.config, self.repo).send_due(limited_send)
                quota.consume_global("send", sent)
                log("scheduler.completed")
            finally:
                conn.execute("SELECT pg_advisory_unlock(20260603)")

__all__ = ["SchedulerService"]
