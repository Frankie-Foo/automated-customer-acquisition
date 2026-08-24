from __future__ import annotations

from typing import Any

from ..db import Repository
from ..sabcd import stage_from_payload
from .pdca import LeadWorkflowService


class LifecycleService:
    STAGES = [
        "lead", "replied", "conversation", "meeting", "business_plan",
        "store_visit", "trial_order", "agency_agreement", "hq_visit",
        "store_creation", "signed", "maintenance", "waiting_pool", "abandoned",
    ]

    def __init__(self, repo: Repository):
        self.repo = repo

    def update(
        self,
        contact_id: int,
        *,
        lifecycle_stage: str | None = None,
        disposition: str | None = None,
        next_action_at: str | None = None,
        notes: str | None = None,
        lost_reason: str | None = None,
        owner: str | None = None,
        sabcd_stage: str | None = None,
    ) -> dict[str, Any]:
        if lifecycle_stage and lifecycle_stage not in self.STAGES:
            raise ValueError(f"Unsupported lifecycle_stage: {lifecycle_stage}")
        sabcd_stage = stage_from_payload(sabcd_stage)
        if disposition and disposition not in {"active", "waiting", "abandoned", "won", "lost"}:
            raise ValueError(f"Unsupported disposition: {disposition}")
        if lifecycle_stage == "abandoned" and not disposition:
            disposition = "abandoned"
        if lifecycle_stage == "signed" and not disposition:
            disposition = "won"
        self.repo.update_lifecycle(
            contact_id,
            lifecycle_stage=lifecycle_stage,
            disposition=disposition,
            next_action_at=next_action_at,
            notes=notes,
            lost_reason=lost_reason,
            owner=owner,
            sabcd_stage=sabcd_stage,
        )
        next_task = None
        if lifecycle_stage or disposition or sabcd_stage:
            self.repo.close_open_followup_tasks(contact_id)
            next_task = LeadWorkflowService(self.repo).ensure_next_task(contact_id)
        return {
            "contact_id": contact_id,
            "lifecycle_stage": lifecycle_stage,
            "disposition": disposition,
            "sabcd_stage": sabcd_stage,
            "next_task": next_task,
        }

__all__ = ["LifecycleService"]
