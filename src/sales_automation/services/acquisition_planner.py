from __future__ import annotations

import re
from itertools import product
from typing import Any, Callable

from ..linkedin_public_search import LinkedInPublicSearchService
from ..logging_utils import log
from ..quotas import QuotaService


def plan_combinations(plan: dict[str, Any]) -> list[dict[str, str]]:
    dimensions = (
        _values(plan.get("regions"), "Global"),
        _values(plan.get("industries"), "luxury retail"),
        _values(plan.get("company_types"), "distributor"),
        _role_values(plan.get("role_terms")),
    )
    rows = [
        {"location": region, "industry": industry, "company_type": company_type, "role": role}
        for region, industry, company_type, role in product(*dimensions)
    ]
    if not rows:
        return []
    start = max(0, int(plan.get("cursor_position") or 0)) % len(rows)
    count = max(1, min(int(plan.get("combinations_per_run") or 3), len(rows)))
    return [rows[(start + offset) % len(rows)] for offset in range(count)]


class AcquisitionPlannerService:
    """Run due region-by-industry plans through the existing audited sourcing path."""

    def __init__(
        self,
        config: Any,
        repo: Any,
        *,
        search_factory: Callable[[Any, Any], Any] = LinkedInPublicSearchService,
        quota_factory: Callable[[Any, Any], Any] = QuotaService,
    ):
        self.config = config
        self.repo = repo
        self.search_factory = search_factory
        self.quota_factory = quota_factory

    def sync_configured_plans(self) -> dict[str, int]:
        section = self.config.raw.get("acquisition", {})
        configured = section.get("plans", []) if isinstance(section, dict) else []
        if not isinstance(configured, list):
            return {"configured": 0, "created": 0, "existing": 0}
        existing_names = {
            str(plan.get("name") or "").strip()
            for plan in self.repo.list_acquisition_plans(limit=500)
        }
        summary = {"configured": len(configured), "created": 0, "existing": 0}
        for item in configured:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            if name in existing_names:
                summary["existing"] += 1
                continue
            self.repo.create_acquisition_plan(
                name=name,
                regions=_values(item.get("regions") or section.get("regions"), "Global"),
                industries=_values(item.get("industries"), "luxury retail"),
                company_types=_values(item.get("company_types"), "distributor"),
                role_terms=_values(item.get("role_terms") or section.get("role_terms"), "owner"),
                owner_user_id=item.get("owner_user_id"),
                pool_type=str(item.get("pool_type") or section.get("pool_type") or "public"),
                daily_lead_limit=int(item.get("daily_lead_limit") or section.get("daily_lead_limit") or 8),
                combinations_per_run=int(item.get("combinations_per_run") or section.get("combinations_per_run") or 8),
            )
            existing_names.add(name)
            summary["created"] += 1
        return summary

    def run_due(self, *, limit: int = 10) -> dict[str, Any]:
        summary = {"plans": 0, "completed": 0, "failed": 0, "results": 0, "promoted": 0}
        for plan in self.repo.list_due_acquisition_plans(limit=max(1, min(int(limit), 50))):
            summary["plans"] += 1
            combinations = plan_combinations(plan)
            run = self.repo.begin_acquisition_plan_run(int(plan["id"]), combinations)
            if not run:
                continue
            try:
                recoverable = hasattr(self.repo, "claim_acquisition_run_item")
                metrics = self._run_plan_recoverable(plan, run) if recoverable else self._run_plan(plan, combinations)
                if recoverable and metrics["pending_items"]:
                    status = "retry_wait"
                    cursor_advance = 0
                elif recoverable and metrics["failed_items"]:
                    status = "completed_partial" if metrics["completed_items"] else "failed"
                    cursor_advance = metrics["items"]
                else:
                    status = "completed"
                    cursor_advance = len(metrics["combinations"])
                self.repo.finish_acquisition_plan_run(
                    int(run["id"]),
                    plan_id=int(plan["id"]),
                    status=status,
                    metrics=metrics,
                    cursor_advance=cursor_advance,
                )
                if status in {"completed", "completed_partial"}:
                    summary["completed"] += 1
                else:
                    summary["failed"] += 1
                summary["results"] += metrics["results"]
                summary["promoted"] += metrics["promoted"]
            except Exception as exc:
                self.repo.finish_acquisition_plan_run(
                    int(run["id"]),
                    plan_id=int(plan["id"]),
                    status="failed",
                    metrics={},
                    error=str(exc)[:2000],
                    cursor_advance=0,
                )
                summary["failed"] += 1
                log("acquisition_plan.failed", plan_id=plan["id"], error=str(exc))
        return summary

    def _run_plan_recoverable(self, plan: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
        owner, pool_type, budget = self._execution_scope(plan)
        search = self.search_factory(self.config, self.repo)
        quota = self.quota_factory(self.config, self.repo)
        existing = self.repo.summarize_acquisition_plan_run(int(run["id"]))
        while existing["pending_items"] and existing["results"] < budget:
            item = self.repo.claim_acquisition_run_item(int(run["id"]))
            if not item:
                break
            remaining_items = max(1, existing["items"] - existing["completed_items"] - existing["failed_items"])
            requested = max(1, (budget - existing["results"]) // remaining_items)
            try:
                result = search.run(item["criteria"], requested, user=owner, pool_type=pool_type)
                used = max(0, int(result.get("results") or 0))
                promoted = max(0, int(result.get("promoted") or 0))
                if used:
                    if pool_type == "public":
                        quota.consume_global("source", used)
                    else:
                        quota.consume(owner, "source", used)
                metrics = {
                    "criteria": item["criteria"],
                    "requested": requested,
                    "results": used,
                    "promoted": promoted,
                }
                if not self.repo.complete_acquisition_run_item(int(item["id"]), item["lease_token"], metrics):
                    raise RuntimeError("acquisition_item_lease_lost")
            except Exception as exc:
                self.repo.retry_acquisition_run_item(int(item["id"]), item["lease_token"], str(exc))
                log("acquisition_plan.item_failed", plan_id=plan["id"], item_id=item["id"], error=str(exc))
            existing = self.repo.summarize_acquisition_plan_run(int(run["id"]))
        return existing

    def _execution_scope(self, plan: dict[str, Any]) -> tuple[dict[str, Any] | None, str, int]:
        pool_type = str(plan.get("pool_type") or "private")
        if pool_type not in {"private", "public"}:
            raise RuntimeError("acquisition_plan_invalid_pool_type")
        owner = None
        quota = self.quota_factory(self.config, self.repo)
        if pool_type == "private":
            owner = self.repo.get_active_user(int(plan.get("owner_user_id") or 0))
            if not owner:
                raise RuntimeError("acquisition_plan_owner_unavailable")
            snapshot = quota.snapshot(owner)
            remaining = min(int(snapshot["source"]["remaining_user"]), int(snapshot["source"]["remaining_global"]))
        else:
            remaining = quota.remaining_global("source")
        return owner, pool_type, min(max(0, int(plan.get("daily_lead_limit") or 0)), remaining)

    def _run_plan(self, plan: dict[str, Any], combinations: list[dict[str, str]]) -> dict[str, Any]:
        pool_type = str(plan.get("pool_type") or "private")
        if pool_type not in {"private", "public"}:
            raise RuntimeError("acquisition_plan_invalid_pool_type")
        owner = None
        if pool_type == "private":
            owner = self.repo.get_active_user(int(plan.get("owner_user_id") or 0))
            if not owner:
                raise RuntimeError("acquisition_plan_owner_unavailable")
        if not combinations:
            return {"results": 0, "promoted": 0, "combinations": []}
        quota = self.quota_factory(self.config, self.repo)
        if pool_type == "public":
            remaining = quota.remaining_global("source")
        else:
            snapshot = quota.snapshot(owner)
            remaining = min(int(snapshot["source"]["remaining_user"]), int(snapshot["source"]["remaining_global"]))
        budget = min(max(0, int(plan.get("daily_lead_limit") or 0)), remaining)
        search = self.search_factory(self.config, self.repo)
        metrics = {"results": 0, "promoted": 0, "combinations": []}
        for index, criteria in enumerate(combinations):
            left = budget - metrics["results"]
            if left <= 0:
                break
            slots = max(1, len(combinations) - index)
            requested = max(1, left // slots)
            result = search.run(criteria, requested, user=owner, pool_type=pool_type)
            used = max(0, int(result.get("results") or 0))
            promoted = max(0, int(result.get("promoted") or 0))
            if used:
                if pool_type == "public":
                    quota.consume_global("source", used)
                else:
                    quota.consume(owner, "source", used)
            metrics["results"] += used
            metrics["promoted"] += promoted
            metrics["combinations"].append({"criteria": criteria, "requested": requested, "results": used, "promoted": promoted})
        return metrics


def _values(value: Any, fallback: str) -> list[str]:
    if isinstance(value, str):
        value = [part.strip() for part in value.replace(";", ",").split(",")]
    if not isinstance(value, list):
        value = []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip())) or [fallback]


def _role_values(value: Any) -> list[str]:
    roles = [
        role.strip()
        for item in _values(value, "owner")
        for role in re.split(r"\s+or\s+", item, flags=re.IGNORECASE)
        if role.strip()
    ]
    return list(dict.fromkeys(roles))


__all__ = ["AcquisitionPlannerService", "plan_combinations"]
