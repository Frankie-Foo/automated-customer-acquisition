from contextlib import contextmanager
from pathlib import Path

from sales_automation.config import AppConfig
from sales_automation.services.scheduler import SchedulerService


class _Connection:
    def execute(self, query):
        return self if "pg_try_advisory_lock" in query else None

    def fetchone(self):
        return {"locked": True}


class _Db:
    @contextmanager
    def connect(self):
        yield _Connection()


class _Repo:
    db = _Db()

    def __init__(self):
        self.finished_loop = None

    def start_system_loop(self):
        return 7

    def finish_system_loop(self, run_id, *, status, result):
        self.finished_loop = (run_id, status, result)

    def close_expired_outreach_sequences(self, **_kwargs):
        return {"waiting": 0, "abandoned": 0}

    def recycle_stale_private_pool(self, **_kwargs):
        return 0


def test_scheduler_uses_paid_contactout_only_after_regular_enrichment(monkeypatch):
    calls = []

    class Acquisition:
        def __init__(self, *_args): pass
        def run_due(self):
            calls.append("acquisition")
            return {"completed": 1}

    class Enrichment:
        def __init__(self, *_args): pass
        def enrich(self, limit):
            calls.append("enrichment")
            return 3, 2

    class ContactOut:
        def __init__(self, *_args): pass
        def auto_enqueue(self, limit):
            calls.append("contactout_queue")
            return {"queued": 1, "candidates": 1, "skipped": [], "jobs": []}
        def run_many(self, limit):
            calls.append("contactout_run")
            return [{"status": "succeeded"}]

    class ApolloPhone:
        def __init__(self, *_args): pass
        def auto_enqueue(self, limit):
            calls.append("apollo_queue")
            return {"queued": 1, "candidates": 1, "jobs": []}
        def dispatch_many(self, limit):
            calls.append("apollo_run")
            return [{"status": "awaiting_webhook"}]

    class Queue:
        def __init__(self, *_args): pass
        def queue(self, limit):
            calls.append("queue")
            return 4

    class Quota:
        def __init__(self, *_args): pass
        def remaining_global(self, kind): return 10
        def consume_global(self, kind, amount): pass

    class Outreach:
        def __init__(self, *_args): pass
        def send_due(self, limit):
            calls.append("send")
            return 4

    class Workflow:
        def __init__(self, *_args): pass
        def refresh_tasks(self, **_kwargs): return 0

    class Flywheel:
        def __init__(self, *_args): pass
        def run_once(self): return {"status": "completed"}

    module = "sales_automation.services.scheduler"
    monkeypatch.setattr(f"{module}.AcquisitionPlannerService", Acquisition)
    monkeypatch.setattr(f"{module}.EnrichmentService", Enrichment)
    monkeypatch.setattr(f"{module}.ContactOutQueueService", ContactOut)
    monkeypatch.setattr(f"{module}.contactout_bridge_configured", lambda _config: True)
    monkeypatch.setattr(f"{module}.ApolloPhoneQueueService", ApolloPhone)
    monkeypatch.setattr(f"{module}.apollo_phone_configured", lambda _config: True)
    monkeypatch.setattr(f"{module}.QueueService", Queue)
    monkeypatch.setattr(f"{module}.QuotaService", Quota)
    monkeypatch.setattr(f"{module}.OutreachService", Outreach)
    monkeypatch.setattr(f"{module}.LeadWorkflowService", Workflow)
    monkeypatch.setattr(f"{module}.DataFlywheelService", Flywheel)

    config = AppConfig(raw={
        "contactout": {"auto_queue_limit": 5, "scheduler_limit": 5},
        "apollo_phone": {"auto_queue_limit": 5, "scheduler_limit": 5},
    }, root_dir=Path("."))
    repo = _Repo()
    result = SchedulerService(config, repo).run_once(25, 25, 25)

    assert calls == ["acquisition", "enrichment", "contactout_queue", "contactout_run", "apollo_queue", "apollo_run", "queue", "send"]
    assert result["enrichment"] == {"succeeded": 3, "failed": 2}
    assert result["queued"] == 4
    assert result["sent"] == 4
    assert repo.finished_loop[0:2] == (7, "completed")
    assert repo.finished_loop[2]["sent"] == 4
    assert repo.finished_loop[2]["flywheel"]["status"] == "completed"


def test_scheduler_continues_after_provider_failure(monkeypatch):
    calls = []

    class Acquisition:
        def __init__(self, *_args): pass
        def run_due(self): raise RuntimeError("source unavailable")

    class Enrichment:
        def __init__(self, *_args): pass
        def enrich(self, _limit): return 0, 0

    class Queue:
        def __init__(self, *_args): pass
        def queue(self, _limit):
            calls.append("queue")
            return 1

    class Quota:
        def __init__(self, *_args): pass
        def remaining_global(self, _kind): return 10
        def consume_global(self, _kind, _amount): pass

    class Outreach:
        def __init__(self, *_args): pass
        def send_due(self, _limit):
            calls.append("send")
            return 1

    class Workflow:
        def __init__(self, *_args): pass
        def refresh_tasks(self, **_kwargs):
            calls.append("tasks")
            return 1

    class Flywheel:
        def __init__(self, *_args): pass
        def run_once(self): return {"status": "completed"}

    module = "sales_automation.services.scheduler"
    monkeypatch.setattr(f"{module}.AcquisitionPlannerService", Acquisition)
    monkeypatch.setattr(f"{module}.EnrichmentService", Enrichment)
    monkeypatch.setattr(f"{module}.contactout_bridge_configured", lambda _config: False)
    monkeypatch.setattr(f"{module}.apollo_phone_configured", lambda _config: False)
    monkeypatch.setattr(f"{module}.QueueService", Queue)
    monkeypatch.setattr(f"{module}.QuotaService", Quota)
    monkeypatch.setattr(f"{module}.OutreachService", Outreach)
    monkeypatch.setattr(f"{module}.LeadWorkflowService", Workflow)
    monkeypatch.setattr(f"{module}.DataFlywheelService", Flywheel)

    result = SchedulerService(AppConfig(raw={}, root_dir=Path(".")), _Repo()).run_once(25, 25, 25)

    assert result["status"] == "completed_with_errors"
    assert result["errors"] == [{"step": "acquisition", "error": "source unavailable"}]
    assert calls == ["queue", "send", "tasks"]
