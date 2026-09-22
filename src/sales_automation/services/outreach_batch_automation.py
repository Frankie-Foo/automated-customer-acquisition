from __future__ import annotations

from typing import Any

from ..config import AppConfig
from ..db import Repository
from ..logging_utils import log
from ..quotas import QuotaService
from .outreach import PersonalizedEmailService
from .quality import OutboundQualityService
from .research import AccountResearchService


class OutreachBatchAutomationService:
    """Runs explicitly enabled outreach batches without an interactive agent."""

    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo

    def run_due(self, limit: int) -> dict[str, Any]:
        limit = max(0, min(int(limit), 25))
        if limit == 0:
            return _result()
        self.repo.db.bind_system_actor()
        self._recover_stale()
        result = _result()
        try:
            for _ in range(limit):
                item = self._claim_next()
                if not item:
                    break
                outcome = self._process(item)
                result[outcome] += 1
                result["processed"] += 1
        finally:
            self.repo.db.bind_system_actor()
            self._complete_finished()
        return result

    def _process(self, item: dict[str, Any]) -> str:
        owner = self.repo.get_user_by_id(int(item["owner_user_id"]))
        if not owner or not owner.get("active", True):
            self._set_state(item["lead_id"], "held", "owner_inactive")
            return "held"
        self.repo.db.bind_actor(owner)
        try:
            if item["claimed_from"] == "ready":
                return self._send(item, owner)
            contact = self.repo.get_private_contact_for_user(int(item["contact_id"]), owner)
            if not contact:
                self._set_state(item["lead_id"], "held", "ownership_changed")
                return "held"
            try:
                AccountResearchService(self.config, self.repo).research(int(contact["id"]), user=owner)
            except RuntimeError as exc:
                reason = _reason("research", exc)
                self._set_state(item["lead_id"], "held", reason)
                return "held"
            contact = self.repo.get_private_contact_for_user(int(item["contact_id"]), owner) or contact
            assessment = OutboundQualityService(self.repo).assess_contact(contact)
            if not assessment.get("qualified"):
                reason = f"icp_{assessment.get('tier', 'review')}:{int(assessment.get('score') or 0)}"
                self._set_state(item["lead_id"], "held", reason)
                return "held"
            draft = PersonalizedEmailService(self.config, self.repo).draft(
                int(contact["id"]), mode="ai", user=owner, campaign_id=int(item["campaign_id"])
            )
            if draft.get("generation_source") == "template":
                self._set_state(item["lead_id"], "retry", "model_unavailable_or_budget_exhausted")
                return "retry"
            review = draft.get("quality_review") if isinstance(draft.get("quality_review"), dict) else {}
            if review.get("status") == "blocked":
                codes = ",".join(str(issue.get("code") or "quality") for issue in review.get("blocking_issues") or [])
                self._set_state(item["lead_id"], "held", f"draft_quality:{codes}"[:500])
                return "held"
            approved = self.repo.approve_latest_email_draft(int(contact["id"]), user_id=int(owner["id"]))
            if not approved:
                self._set_state(item["lead_id"], "retry", "draft_approval_failed")
                return "retry"
            self._set_state(item["lead_id"], "ready", None)
            item["claimed_from"] = "ready"
            return self._send(item, owner)
        except ValueError as exc:
            self._set_state(item["lead_id"], "held", _reason("blocked", exc))
            return "held"
        except Exception as exc:
            self._set_state(item["lead_id"], "retry", _reason("runtime", exc))
            log("outreach_batch.item_failed", lead_id=item["lead_id"], error=type(exc).__name__)
            return "retry"
        finally:
            self.repo.db.bind_system_actor()

    def _send(self, item: dict[str, Any], owner: dict[str, Any]) -> str:
        quota = QuotaService(self.config, self.repo)
        snapshot = quota.snapshot(owner)
        if min(snapshot["send"]["remaining_user"], snapshot["send"]["remaining_global"]) <= 0:
            self._set_state(item["lead_id"], "ready", "daily_send_quota_exhausted")
            return "deferred"
        approved = self.repo.get_latest_email_draft(int(item["contact_id"]), user_id=int(owner["id"]))
        if not approved or approved.get("status") != "approved" or int(approved.get("campaign_id") or 0) != int(item["campaign_id"]):
            self._set_state(item["lead_id"], "retry", "approved_campaign_draft_missing")
            return "retry"
        self._set_state(item["lead_id"], "sending", None)
        try:
            sent = PersonalizedEmailService(self.config, self.repo).send(
                int(item["contact_id"]),
                subject=str(approved.get("subject") or ""),
                body=str(approved.get("body") or ""),
                mode="ai",
                user=owner,
                cc_emails=_audit_cc(item.get("automation_config")),
            )
        except RuntimeError as exc:
            if str(exc).startswith("send_step_already_sent"):
                self._set_state(item["lead_id"], "sent", "idempotent_existing_send")
                return "sent"
            raise
        if not sent.get("sent"):
            self._set_state(item["lead_id"], "retry", "provider_did_not_record_send")
            return "retry"
        quota.consume(owner, "send", 1)
        self._set_state(item["lead_id"], "sent", None)
        return "sent"

    def _claim_next(self) -> dict[str, Any] | None:
        with self.repo.db.connect() as conn:
            row = conn.execute(
                """WITH candidate AS (
                     SELECT l.id AS lead_id, l.contact_id, l.campaign_id, l.automation_status AS claimed_from,
                            b.owner_user_id, b.automation_config
                     FROM leads l JOIN campaigns b ON b.id=l.campaign_id
                     WHERE b.channel='outreach_batch' AND b.automation_status='running'
                       AND l.contact_id IS NOT NULL
                       AND (
                         l.automation_status='pending'
                         OR (l.automation_status='ready' AND NOT (
                           l.automation_reason='daily_send_quota_exhausted'
                           AND l.automation_updated_at::date=CURRENT_DATE
                         ))
                         OR (l.automation_status='retry'
                             AND l.automation_updated_at < NOW() - INTERVAL '6 hours')
                       )
                     ORDER BY CASE l.automation_status WHEN 'ready' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,
                              b.id, l.source_row NULLS LAST, l.id
                     FOR UPDATE OF l SKIP LOCKED LIMIT 1
                   )
                   UPDATE leads l SET
                     automation_status=CASE WHEN candidate.claimed_from='ready' THEN 'sending' ELSE 'researching' END,
                     automation_attempts=l.automation_attempts+1,
                     automation_reason=NULL, automation_updated_at=NOW()
                   FROM candidate WHERE l.id=candidate.lead_id
                   RETURNING candidate.*"""
            ).fetchone()
        return row

    def _set_state(self, lead_id: int, status: str, reason: str | None) -> None:
        with self.repo.db.connect() as conn:
            conn.execute(
                """UPDATE leads SET automation_status=%s, automation_reason=%s,
                          automation_updated_at=NOW() WHERE id=%s""",
                (status, reason, int(lead_id)),
            )

    def _recover_stale(self) -> None:
        with self.repo.db.connect() as conn:
            conn.execute(
                """UPDATE leads SET automation_status='retry', automation_reason='stale_worker_recovered',
                          automation_updated_at=NOW() - INTERVAL '6 hours'
                   WHERE automation_status IN ('researching','sending')
                     AND automation_updated_at < NOW() - INTERVAL '30 minutes'"""
            )

    def _complete_finished(self) -> None:
        with self.repo.db.connect() as conn:
            conn.execute(
                """UPDATE campaigns b SET automation_status='completed', automation_completed_at=NOW(),
                          automation_updated_at=NOW()
                   WHERE b.channel='outreach_batch' AND b.automation_status='running'
                     AND NOT EXISTS (
                       SELECT 1 FROM leads l WHERE l.campaign_id=b.id
                         AND l.automation_status IN ('pending','researching','ready','sending','retry')
                     )"""
            )


def _audit_cc(config: Any) -> list[str]:
    if not isinstance(config, dict):
        return []
    return [str(value) for value in config.get("audit_cc") or []]


def _reason(prefix: str, exc: Exception) -> str:
    return f"{prefix}:{type(exc).__name__}:{str(exc)}"[:500]


def _result() -> dict[str, int]:
    return {"processed": 0, "sent": 0, "held": 0, "retry": 0, "deferred": 0}


__all__ = ["OutreachBatchAutomationService"]
