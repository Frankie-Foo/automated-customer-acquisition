from __future__ import annotations

from types import SimpleNamespace

from sales_automation.services import automation
from sales_automation.services.automation import AutomationRunService


class FakeRepo:
    def __init__(self, *, pause_after_first: bool = False):
        self.pause_after_first = pause_after_first
        self.progress = []
        self.finished = []
        self.run = {
            "id": 7,
            "owner_user_id": 2,
            "status": "queued",
            "progress_current": 0,
            "input_payload": {
                "seeds": [{"company_name": "A"}, {"company_name": "B"}],
                "per_company_limit": 2,
            },
            "result": {},
        }

    def claim_automation_run(self, run_id):
        assert run_id == 7
        if self.run["status"] != "queued":
            return None
        self.run["status"] = "running"
        return dict(self.run)

    def get_active_user(self, user_id):
        return {"id": user_id, "role": "sales", "daily_source_limit": 100, "daily_send_limit": 100}

    def get_automation_run(self, run_id, user=None):
        if self.pause_after_first and self.progress and len(self.progress) == 1:
            return {**self.run, "pause_requested": True}
        return {**self.run, "pause_requested": False}

    def update_automation_run_progress(self, run_id, *, progress_current, result, stage="sourcing"):
        self.progress.append((progress_current, dict(result)))
        self.run.update(progress_current=progress_current, result=dict(result), stage=stage)

    def finish_automation_run(self, run_id, *, status, result=None, error=None):
        self.finished.append((status, result, error))
        self.run.update(status=status, result=result or self.run.get("result", {}), error=error)

    def list_contacts_for_search_tasks(self, task_ids):
        return []

    def auto_assign_public_pool(self, *, limit, contact_ids=None):
        return {"assigned": 0, "skipped": 0, "missing_rules": True}


class FakeSearchService:
    def __init__(self, config, repo):
        self.repo = repo

    def run_company_seeds(self, seeds, *, per_company_limit, user, auto_queue):
        name = seeds[0]["company_name"]
        return {
            "companies": 1,
            "tasks": [{"task_id": 10 if name == "A" else 11}],
            "results": 2,
            "promoted": 1,
            "skipped": 1,
            "phone_attached": 0,
        }


class FakeQuotaService:
    consumed = []

    def __init__(self, config, repo):
        pass

    def consume(self, user, action, amount):
        self.consumed.append((user["id"], action, amount))


def test_automation_run_processes_each_company_and_waits_for_approval(monkeypatch):
    repo = FakeRepo()
    FakeQuotaService.consumed = []
    monkeypatch.setattr(automation, "LinkedInPublicSearchService", FakeSearchService)
    monkeypatch.setattr(automation, "QuotaService", FakeQuotaService)

    result = AutomationRunService(SimpleNamespace(), repo).process(7)

    assert [item[0] for item in repo.progress] == [1, 2, 2]
    assert repo.finished[-1][0] == "awaiting_approval"
    assert result["result"]["promoted"] == 2
    assert FakeQuotaService.consumed == [(2, "source", 2), (2, "source", 2)]


def test_automation_run_honors_pause_between_companies(monkeypatch):
    repo = FakeRepo(pause_after_first=True)
    FakeQuotaService.consumed = []
    monkeypatch.setattr(automation, "LinkedInPublicSearchService", FakeSearchService)
    monkeypatch.setattr(automation, "QuotaService", FakeQuotaService)

    result = AutomationRunService(SimpleNamespace(), repo).process(7)

    assert [item[0] for item in repo.progress] == [1]
    assert repo.finished[-1][0] == "paused"
    assert result["status"] == "paused"


def test_automation_postprocess_profiles_assigns_and_prepares_draft(monkeypatch):
    class PrepRepo(FakeRepo):
        def __init__(self):
            super().__init__()
            self.assigned = False

        def list_contacts_for_search_tasks(self, task_ids):
            return [{
                "id": 51,
                "pool_type": "private" if self.assigned else "public",
                "owner_user_id": 3 if self.assigned else None,
                "email": "lead@example.com",
                "email_status": "valid",
            }]

        def auto_assign_public_pool(self, *, limit, contact_ids=None):
            self.assigned = True
            return {"assigned": 1, "skipped": 0, "missing_rules": False}

    calls = []

    class Profile:
        def __init__(self, config, repo):
            pass

        def summarize(self, contact_id, *, use_llm=True):
            calls.append(("profile", contact_id, use_llm))

    class Research:
        def __init__(self, config, repo):
            pass

        def research(self, contact_id, *, user):
            calls.append(("research", contact_id, user["id"]))

    class Draft:
        def __init__(self, config, repo):
            pass

        def draft(self, contact_id, *, user):
            calls.append(("draft", contact_id, user["id"]))

    repo = PrepRepo()
    monkeypatch.setattr(automation, "ProfileAgentService", Profile)
    monkeypatch.setattr(automation, "AccountResearchService", Research)
    monkeypatch.setattr(automation, "PersonalizedEmailService", Draft)
    service = AutomationRunService(SimpleNamespace(raw={"automation": {"max_auto_prepare_drafts": 5}}), repo)

    result = service._prepare_results(
        7,
        {"tasks": [{"task_id": 91}]},
        owner={"id": 2},
        payload={"seeds": [{"company_name": "A"}], "auto_prepare_drafts": True},
    )

    assert result["profiled"] == 1
    assert result["assignment"]["assigned"] == 1
    assert result["drafted"] == 1
    assert calls == [("profile", 51, False), ("research", 51, 3), ("draft", 51, 3)]
