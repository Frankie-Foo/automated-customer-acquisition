from __future__ import annotations

from ..config import AppConfig
from ..contactout_queue import ContactOutQueueService
from ..db import Repository
from ..logging_utils import log
from ..quotas import QuotaService
from .enrichment import EnrichmentService
from .acquisition_planner import AcquisitionPlannerService
from .flywheel import DataFlywheelService
from .outreach import OutreachService
from .pdca import LeadWorkflowService
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
                acquisition = AcquisitionPlannerService(self.config, self.repo).run_due()
                contactout = []
                for _ in range(max(0, min(50, int(self.config.raw.get("contactout", {}).get("scheduler_limit") or 0)))):
                    run = ContactOutQueueService(self.config, self.repo).run_next()
                    if not run:
                        break
                    contactout.append(vars(run))
                quota = QuotaService(self.config, self.repo)
                EnrichmentService(self.config, self.repo).enrich(enrich_limit)
                QueueService(self.repo).queue(queue_limit)
                limited_send = min(send_limit, quota.remaining_global("send"))
                sent = OutreachService(self.config, self.repo).send_due(limited_send)
                quota.consume_global("send", sent)
                wait_days = int(self.config.raw.get("outreach", {}).get("waiting_pool_after_days") or 14)
                closed = self.repo.close_expired_outreach_sequences(wait_days=wait_days, limit=max(100, send_limit))
                recycled = self.repo.recycle_stale_private_pool(limit=max(100, queue_limit))
                tasks = LeadWorkflowService(self.repo).refresh_tasks(limit=max(500, queue_limit))
                try:
                    flywheel = DataFlywheelService(self.config, self.repo).run_once()
                except Exception as exc:
                    flywheel = {"status": "failed", "error": str(exc)[:500]}
                    log("flywheel.failed", error=str(exc))
                log("scheduler.completed", acquisition=acquisition, contactout=contactout, sent=sent, waiting=closed["waiting"], abandoned=closed["abandoned"], recycled=recycled, tasks=tasks, flywheel=flywheel)
            finally:
                conn.execute("SELECT pg_advisory_unlock(20260603)")

__all__ = ["SchedulerService"]
