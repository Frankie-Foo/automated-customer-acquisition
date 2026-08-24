from __future__ import annotations

from ..config import AppConfig
from ..apollo_phone import ApolloPhoneQueueService, apollo_phone_configured
from ..contactout_queue import ContactOutQueueService, contactout_bridge_configured
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

    def run_once(self, enrich_limit: int, queue_limit: int, send_limit: int) -> dict:
        with self.repo.db.connect() as conn:
            row = conn.execute("SELECT pg_try_advisory_lock(20260603) AS locked").fetchone()
            if not row["locked"]:
                log("scheduler.skipped_locked")
                return {"status": "skipped_locked"}
            if hasattr(conn, "commit"):
                conn.commit()
            errors: list[dict[str, str]] = []

            def step(name: str, callback, fallback):
                try:
                    return callback()
                except Exception as exc:
                    error = {"step": name, "error": str(exc)[:500]}
                    errors.append(error)
                    log("scheduler.step_failed", **error)
                    return fallback

            try:
                acquisition = step(
                    "acquisition",
                    lambda: AcquisitionPlannerService(self.config, self.repo).run_due(),
                    {"completed": 0, "failed": 1},
                )
                enrichment_ok, enrichment_failed = step(
                    "enrichment",
                    lambda: EnrichmentService(self.config, self.repo).enrich(enrich_limit),
                    (0, 0),
                )
                contactout_config = self.config.raw.get("contactout", {})
                contactout_limit = int(contactout_config.get("scheduler_limit") or 0)
                contactout_service = ContactOutQueueService(self.config, self.repo)
                contactout_auto_queue_limit = int(contactout_config.get("auto_queue_limit") or contactout_limit or 0)
                if not contactout_bridge_configured(self.config):
                    contactout_auto_queue = {"queued": 0, "candidates": 0, "skipped": [], "jobs": [], "reason": "bridge_unconfigured"}
                    contactout = []
                else:
                    contactout_auto_queue = (
                        step(
                            "contactout_auto_queue",
                            lambda: contactout_service.auto_enqueue(contactout_auto_queue_limit),
                            {"queued": 0, "candidates": 0, "skipped": [], "jobs": []},
                        )
                        if contactout_auto_queue_limit > 0 else {"queued": 0, "candidates": 0, "skipped": [], "jobs": []}
                    )
                    contactout = step(
                        "contactout",
                        lambda: contactout_service.run_many(contactout_limit),
                        [],
                    ) if contactout_limit > 0 else []
                apollo_config = self.config.raw.get("apollo_phone", {})
                apollo_limit = int(apollo_config.get("scheduler_limit") or 0)
                apollo_auto_queue_limit = int(apollo_config.get("auto_queue_limit") or apollo_limit or 0)
                if not apollo_phone_configured(self.config):
                    apollo_auto_queue = {"queued": 0, "candidates": 0, "jobs": [], "reason": "apollo_unconfigured"}
                    apollo_phone = []
                else:
                    apollo_service = ApolloPhoneQueueService(self.config, self.repo)
                    apollo_auto_queue = (
                        step(
                            "apollo_phone_auto_queue",
                            lambda: apollo_service.auto_enqueue(apollo_auto_queue_limit),
                            {"queued": 0, "candidates": 0, "jobs": []},
                        )
                        if apollo_auto_queue_limit > 0 else {"queued": 0, "candidates": 0, "jobs": []}
                    )
                    apollo_phone = step(
                        "apollo_phone",
                        lambda: apollo_service.dispatch_many(apollo_limit),
                        [],
                    ) if apollo_limit > 0 else []
                quota = QuotaService(self.config, self.repo)
                queued = step("queue", lambda: QueueService(self.repo).queue(queue_limit), 0)

                def send_due() -> int:
                    limited_send = min(send_limit, quota.remaining_global("send"))
                    sent_count = OutreachService(self.config, self.repo).send_due(limited_send)
                    quota.consume_global("send", sent_count)
                    return sent_count

                sent = step("send", send_due, 0)
                wait_days = int(self.config.raw.get("outreach", {}).get("waiting_pool_after_days") or 14)
                closed = step(
                    "sequence_close",
                    lambda: self.repo.close_expired_outreach_sequences(
                        wait_days=wait_days, limit=max(100, send_limit)
                    ),
                    {"waiting": 0, "abandoned": 0},
                )
                recycled = step(
                    "pool_recycle",
                    lambda: self.repo.recycle_stale_private_pool(limit=max(100, queue_limit)),
                    0,
                )
                tasks = step(
                    "tasks",
                    lambda: LeadWorkflowService(self.repo).refresh_tasks(limit=max(500, queue_limit)),
                    0,
                )
                flywheel = step(
                    "flywheel",
                    lambda: DataFlywheelService(self.config, self.repo).run_once(),
                    {"status": "failed"},
                )
                result = {
                    "status": "completed_with_errors" if errors else "completed",
                    "errors": errors,
                    "acquisition": acquisition,
                    "enrichment": {"succeeded": enrichment_ok, "failed": enrichment_failed},
                    "contactout_auto_queue": contactout_auto_queue,
                    "contactout": contactout,
                    "apollo_phone_auto_queue": apollo_auto_queue,
                    "apollo_phone": apollo_phone,
                    "queued": queued,
                    "sent": sent,
                    "waiting": closed["waiting"],
                    "abandoned": closed["abandoned"],
                    "recycled": recycled,
                    "tasks": tasks,
                    "flywheel": flywheel,
                }
                log("scheduler.completed", **result)
                return result
            finally:
                conn.execute("SELECT pg_advisory_unlock(20260603)")
                if hasattr(conn, "commit"):
                    conn.commit()

__all__ = ["SchedulerService"]
