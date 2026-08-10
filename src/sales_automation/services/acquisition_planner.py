from __future__ import annotations

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
        _values(plan.get("role_terms"), "owner OR founder OR managing director"),
    )
    rows = [
        {"location": region, "industry": industry, "company_keyword": company_type, "role": role}
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

    def run_due(self, *, limit: int = 10) -> dict[str, Any]:
        summary = {"plans": 0, "completed": 0, "failed": 0, "results": 0, "promoted": 0}
        for plan in self.repo.list_due_acquisition_plans(limit=max(1, min(int(limit), 50))):
            summary["plans"] += 1
            combinations = plan_combinations(plan)
            run = self.repo.begin_acquisition_plan_run(int(plan["id"]), combinations)
            if not run:
                continue
            try:
                metrics = self._run_plan(plan, combinations)
                self.repo.finish_acquisition_plan_run(
                    int(run["id"]),
                    plan_id=int(plan["id"]),
                    status="completed",
                    metrics=metrics,
                    cursor_advance=len(combinations),
                )
                summary["completed"] += 1
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

    def _run_plan(self, plan: dict[str, Any], combinations: list[dict[str, str]]) -> dict[str, Any]:
        owner = self.repo.get_active_user(int(plan["owner_user_id"]))
        if not owner:
            raise RuntimeError("acquisition_plan_owner_unavailable")
        if not combinations:
            return {"results": 0, "promoted": 0, "combinations": []}
        quota = self.quota_factory(self.config, self.repo)
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
            result = search.run(criteria, requested, user=owner)
            used = max(0, int(result.get("results") or 0))
            promoted = max(0, int(result.get("promoted") or 0))
            if used:
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


__all__ = ["AcquisitionPlannerService", "plan_combinations"]
