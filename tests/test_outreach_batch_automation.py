from types import SimpleNamespace
from unittest.mock import Mock

from sales_automation.services import outreach_batch_automation as module


OWNER = {"id": 2, "role": "sales", "active": True, "daily_send_limit": 80}
ITEM = {
    "lead_id": 11,
    "contact_id": 22,
    "campaign_id": 10,
    "owner_user_id": 2,
    "claimed_from": "pending",
    "automation_config": {"audit_cc": ["frank.fu@vertu.cn"]},
}


class FakeDb:
    def __init__(self):
        self.bound = []

    def bind_actor(self, user):
        self.bound.append(user)

    def bind_system_actor(self):
        self.bound.append("system")


def repo():
    value = Mock()
    value.db = FakeDb()
    value.get_user_by_id.return_value = OWNER
    value.get_private_contact_for_user.return_value = {
        "id": 22, "owner_user_id": 2, "pool_type": "private", "first_name": "Ada",
        "company_name": "Luxury Retail", "job_title": "Commercial Director",
        "email": "ada@example.test", "email_status": "valid", "industry": "luxury retail",
    }
    value.approve_latest_email_draft.return_value = {"id": 33, "status": "approved"}
    value.get_latest_email_draft.return_value = {
        "id": 33, "status": "approved", "campaign_id": 10, "subject": "VERTU partnership", "body": "Body",
    }
    return value


def install_happy_path(monkeypatch, value, *, qualified=True, remaining=10):
    monkeypatch.setattr(module, "AccountResearchService", Mock(return_value=Mock(research=Mock(return_value={}))))
    monkeypatch.setattr(
        module, "OutboundQualityService",
        Mock(return_value=Mock(assess_contact=Mock(return_value={"qualified": qualified, "tier": "qualified" if qualified else "review", "score": 80 if qualified else 55}))),
    )
    mail = Mock()
    mail.draft.return_value = {"generation_source": "deepseek:chat", "quality_review": {"status": "passed"}}
    mail.send.return_value = {"sent": True}
    monkeypatch.setattr(module, "PersonalizedEmailService", Mock(return_value=mail))
    quota = Mock()
    quota.snapshot.return_value = {"send": {"remaining_user": remaining, "remaining_global": remaining}}
    monkeypatch.setattr(module, "QuotaService", Mock(return_value=quota))
    return mail, quota


def test_process_researches_qualifies_and_sends_with_audit_cc(monkeypatch):
    value = repo()
    mail, quota = install_happy_path(monkeypatch, value)
    service = module.OutreachBatchAutomationService(SimpleNamespace(raw={}), value)
    states = []
    service._set_state = lambda lead_id, status, reason: states.append((lead_id, status, reason))

    outcome = service._process(dict(ITEM))

    assert outcome == "sent"
    assert mail.draft.call_args.kwargs["campaign_id"] == 10
    assert mail.send.call_args.kwargs["cc_emails"] == ["frank.fu@vertu.cn"]
    quota.consume.assert_called_once_with(OWNER, "send", 1)
    assert states[-1] == (11, "sent", None)


def test_process_holds_unqualified_contact_without_drafting(monkeypatch):
    value = repo()
    mail, _ = install_happy_path(monkeypatch, value, qualified=False)
    service = module.OutreachBatchAutomationService(SimpleNamespace(raw={}), value)
    states = []
    service._set_state = lambda lead_id, status, reason: states.append((lead_id, status, reason))

    outcome = service._process(dict(ITEM))

    assert outcome == "held"
    mail.draft.assert_not_called()
    assert states[-1] == (11, "held", "icp_review:55")


def test_research_provider_failure_pauses_batch(monkeypatch):
    value = repo()
    research = Mock()
    research.research.side_effect = RuntimeError("provider unavailable")
    monkeypatch.setattr(module, "AccountResearchService", Mock(return_value=research))
    service = module.OutreachBatchAutomationService(SimpleNamespace(raw={}), value)
    states = []
    service._set_state = lambda lead_id, status, reason: states.append((lead_id, status, reason))
    service._pause_campaign = Mock()

    outcome = service._process(dict(ITEM))

    assert outcome == "retry"
    assert states == [(11, "retry", "research:RuntimeError:provider unavailable")]
    service._pause_campaign.assert_called_once_with(10, "research:RuntimeError:provider unavailable")


def test_ready_contact_waits_without_sending_when_daily_quota_is_exhausted(monkeypatch):
    value = repo()
    mail, quota = install_happy_path(monkeypatch, value, remaining=0)
    service = module.OutreachBatchAutomationService(SimpleNamespace(raw={}), value)
    states = []
    service._set_state = lambda lead_id, status, reason: states.append((lead_id, status, reason))
    item = {**ITEM, "claimed_from": "ready"}

    outcome = service._process(item)

    assert outcome == "deferred"
    mail.send.assert_not_called()
    quota.consume.assert_not_called()
    assert states[-1] == (11, "ready", "daily_send_quota_exhausted")


def test_run_continues_researching_after_one_contact_is_deferred():
    value = repo()
    service = module.OutreachBatchAutomationService(SimpleNamespace(raw={}), value)
    items = iter([dict(ITEM), {**ITEM, "lead_id": 12, "contact_id": 23}, None])
    service._claim_next = lambda: next(items)
    outcomes = iter(["deferred", "held"])
    service._process = lambda _item: next(outcomes)
    service._recover_stale = Mock()
    service._complete_finished = Mock()

    result = service.run_due(3)

    assert result == {"processed": 2, "sent": 0, "held": 1, "retry": 0, "deferred": 1}
