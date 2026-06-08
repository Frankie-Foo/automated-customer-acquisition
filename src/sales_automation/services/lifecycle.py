from __future__ import annotations

from typing import Any

from ..db import Repository


class LifecycleService:
    STAGES = [
        "lead", "replied", "conversation", "meeting", "business_plan",
        "store_visit", "trial_order", "agency_agreement", "hq_visit",
        "signed", "maintenance", "waiting_pool", "abandoned",
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
    ) -> dict[str, Any]:
        if lifecycle_stage and lifecycle_stage not in self.STAGES:
            raise ValueError(f"Unsupported lifecycle_stage: {lifecycle_stage}")
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
        )
        return {"contact_id": contact_id, "lifecycle_stage": lifecycle_stage, "disposition": disposition}

__all__ = ["LifecycleService"]
