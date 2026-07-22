from __future__ import annotations

import threading
from typing import Any

from ..config import AppConfig
from ..db import Repository
from ..linkedin_public_search import LinkedInPublicSearchService
from ..logging_utils import log
from ..quotas import QuotaService
from .ai_agents import ProfileAgentService
from .outreach import PersonalizedEmailService
from .pdca import LeadWorkflowService
from .research import AccountResearchService


class AutomationRunService:
    """Durable, resumable orchestration for long-running sourcing imports."""

    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo

    def create_company_seed_run(
        self,
        seeds: list[dict[str, Any]],
        *,
        per_company_limit: int,
        user: dict[str, Any],
        idempotency_key: str,
        auto_prepare_drafts: bool = True,
    ) -> dict[str, Any]:
        if not seeds:
            raise ValueError("No company seeds found in the uploaded file")
        per_company_limit = max(1, min(int(per_company_limit), 20))
        requested = len(seeds) * per_company_limit
        snapshot = QuotaService(self.config, self.repo).snapshot(user)
        remaining = min(snapshot["source"]["remaining_user"], snapshot["source"]["remaining_global"])
        if requested > remaining:
            raise RuntimeError(f"今日获客额度不足：需要 {requested}，剩余 {remaining}")
        run = self.repo.create_automation_run(
            run_type="company_seed_import",
            input_payload={
                "seeds": seeds,
                "per_company_limit": per_company_limit,
                "auto_prepare_drafts": bool(auto_prepare_drafts),
            },
            progress_total=len(seeds),
            user=user,
            idempotency_key=idempotency_key,
        )
        if run.get("status") == "queued":
            self.start(int(run["id"]), user=user)
        return run

    def start(self, run_id: int, *, user: dict[str, Any] | None = None) -> None:
        worker = threading.Thread(
            target=self.process,
            kwargs={"run_id": run_id, "user": user},
            name=f"automation-run-{run_id}",
            daemon=True,
        )
        worker.start()

    def process(self, run_id: int, *, user: dict[str, Any] | None = None) -> dict[str, Any] | None:
        run = self.repo.claim_automation_run(run_id)
        if not run:
            return self.repo.get_automation_run(run_id, user=user)
        owner = user or self.repo.get_active_user(int(run["owner_user_id"]))
        if not owner:
            self.repo.finish_automation_run(run_id, status="failed", error="run_owner_unavailable")
            return self.repo.get_automation_run(run_id)

        payload = run.get("input_payload") or {}
        seeds = list(payload.get("seeds") or [])
        per_company_limit = max(1, int(payload.get("per_company_limit") or 5))
        current = int(run.get("progress_current") or 0)
        result = dict(run.get("result") or {})
        result.setdefault("companies", len(seeds))
        result.setdefault("tasks", [])
        for field in ("results", "promoted", "skipped", "phone_attached", "hiring_signals"):
            result.setdefault(field, 0)

        try:
            for index in range(current, len(seeds)):
                control = self.repo.get_automation_run(run_id)
                if not control or control.get("pause_requested") or control.get("status") == "paused":
                    self.repo.finish_automation_run(run_id, status="paused", result=result)
                    log("automation.paused", run_id=run_id, progress=index)
                    return self.repo.get_automation_run(run_id)

                batch = LinkedInPublicSearchService(self.config, self.repo).run_company_seeds(
                    [seeds[index]],
                    per_company_limit=per_company_limit,
                    user=owner,
                    auto_queue=False,
                )
                result["tasks"].extend(batch.get("tasks") or [])
                for field in ("results", "promoted", "skipped", "phone_attached", "hiring_signals"):
                    result[field] = int(result.get(field) or 0) + int(batch.get(field) or 0)
                used = int(batch.get("results") or 0)
                if used:
                    QuotaService(self.config, self.repo).consume(owner, "source", used)
                self.repo.update_automation_run_progress(
                    run_id,
                    progress_current=index + 1,
                    result=result,
                )

            result = self._prepare_results(run_id, result, owner=owner, payload=payload)
            self.repo.finish_automation_run(run_id, status="awaiting_approval", result=result)
            log("automation.awaiting_approval", run_id=run_id, promoted=result.get("promoted"))
        except Exception as exc:
            self.repo.finish_automation_run(run_id, status="failed", result=result, error=str(exc)[:2000])
            log("automation.failed", run_id=run_id, error=str(exc))
        return self.repo.get_automation_run(run_id)

    def _prepare_results(
        self,
        run_id: int,
        result: dict[str, Any],
        *,
        owner: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        task_ids = [int(item["task_id"]) for item in result.get("tasks", []) if item.get("task_id")]
        contacts = self.repo.list_contacts_for_search_tasks(task_ids)
        contact_ids = [int(contact["id"]) for contact in contacts]
        result.update(profiled=0, drafted=0, preparation_errors=[])

        for contact in contacts:
            try:
                ProfileAgentService(self.config, self.repo).summarize(int(contact["id"]), use_llm=False)
                result["profiled"] += 1
            except Exception as exc:
                result["preparation_errors"].append({"contact_id": contact["id"], "stage": "profile", "error": str(exc)[:300]})

        if owner.get("role") == "sales":
            owner_name = str(owner.get("display_name") or owner.get("username") or f"User {owner['id']}")
            assignment = self.repo.assign_public_contacts_to_owner(
                contact_ids,
                owner_user_id=int(owner["id"]),
                owner_name=owner_name,
            )
        else:
            assignment = self.repo.auto_assign_public_pool(limit=max(len(contact_ids), 1), contact_ids=contact_ids)
        result["assignment"] = assignment
        workflow = LeadWorkflowService(self.repo).register_contacts(
            contact_ids,
            user=owner,
            source_type="company_seed_import",
            source_ref=f"automation_run:{run_id}",
        )
        result["workflow_linked"] = workflow["linked"]
        result["tasks_created"] = workflow["tasks_created"]
        if not payload.get("auto_prepare_drafts", True) or not assignment.get("assigned"):
            self.repo.update_automation_run_progress(
                run_id,
                progress_current=len(payload.get("seeds") or []),
                result=result,
                stage="review",
            )
            return result

        max_drafts = max(0, int(self.config.raw.get("automation", {}).get("max_auto_prepare_drafts") or 20))
        prepared = 0
        for contact in self.repo.list_contacts_for_search_tasks(task_ids):
            if prepared >= max_drafts:
                break
            if contact.get("pool_type") != "private" or not contact.get("owner_user_id"):
                continue
            if contact.get("email_status") != "valid" or not contact.get("email"):
                continue
            assigned_user = self.repo.get_active_user(int(contact["owner_user_id"]))
            if not assigned_user:
                continue
            try:
                AccountResearchService(self.config, self.repo).research(int(contact["id"]), user=assigned_user)
                PersonalizedEmailService(self.config, self.repo).draft(int(contact["id"]), user=assigned_user)
                result["drafted"] += 1
                prepared += 1
            except Exception as exc:
                result["preparation_errors"].append({"contact_id": contact["id"], "stage": "draft", "error": str(exc)[:300]})

        self.repo.update_automation_run_progress(
            run_id,
            progress_current=len(payload.get("seeds") or []),
            result=result,
            stage="review",
        )
        return result

    def pause(self, run_id: int, *, user: dict[str, Any]) -> dict[str, Any]:
        run = self.repo.request_automation_pause(run_id, user=user)
        if not run:
            raise RuntimeError("Task is not running or cannot be paused")
        return run

    def resume(self, run_id: int, *, user: dict[str, Any]) -> dict[str, Any]:
        run = self.repo.resume_automation_run(run_id, user=user)
        if not run:
            raise RuntimeError("Task cannot be resumed")
        owner = self.repo.get_active_user(int(run["owner_user_id"]))
        self.start(run_id, user=owner)
        return run


__all__ = ["AutomationRunService"]
