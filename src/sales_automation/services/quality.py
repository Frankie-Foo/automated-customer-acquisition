from __future__ import annotations

from typing import Any, Iterable

from ..outbound_quality import assess_icp, review_email_copy, score_lead_list


class OutboundQualityService:
    """One interface for ICP assessment, list quality and draft review."""

    def __init__(self, repo: Any):
        self.repo = repo

    def assess_contact(self, contact: dict[str, Any], *, persist: bool = True) -> dict[str, Any]:
        owner_user_id = contact.get("owner_user_id")
        profile = (
            self.repo.get_active_icp_profile(owner_user_id=owner_user_id)
            if hasattr(self.repo, "get_active_icp_profile")
            else None
        )
        assessment = assess_icp(contact, profile)
        if persist and contact.get("id") and hasattr(self.repo, "update_contact_icp_assessment"):
            self.repo.update_contact_icp_assessment(
                int(contact["id"]),
                assessment,
                profile_id=profile.get("id") if profile else None,
            )
        return assessment

    def score_list(self, contacts: Iterable[dict[str, Any]]) -> dict[str, Any]:
        rows = list(contacts)
        profile = self.repo.get_active_icp_profile() if hasattr(self.repo, "get_active_icp_profile") else None
        return score_lead_list(rows, profile)

    def review_draft(self, subject: str, body: str) -> dict[str, Any]:
        return review_email_copy(subject, body)

    def experiment_assignment(
        self,
        *,
        contact_id: int,
        owner_user_id: int | None,
    ) -> dict[str, Any] | None:
        if not hasattr(self.repo, "get_active_outbound_experiment"):
            return None
        experiment = self.repo.get_active_outbound_experiment(owner_user_id=owner_user_id)
        if not experiment:
            return None
        variants = experiment.get("variants") if isinstance(experiment.get("variants"), list) else []
        if len(variants) < 2:
            return None
        variant = variants[contact_id % len(variants)]
        if isinstance(variant, str):
            variant = {"name": variant}
        if not isinstance(variant, dict):
            return None
        return {
            "experiment_id": int(experiment["id"]),
            "experiment_name": experiment.get("name"),
            "variant": str(variant.get("name") or f"Variant {(contact_id % len(variants)) + 1}"),
            "instruction": str(variant.get("instruction") or "")[:500],
            "variable_name": experiment.get("variable_name"),
        }


__all__ = ["OutboundQualityService"]
