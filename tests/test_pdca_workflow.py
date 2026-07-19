from __future__ import annotations

from datetime import UTC, datetime

from sales_automation.services.pdca import (
    LeadWorkflowService,
    dedupe_key,
    next_task_for_contact,
    normalize_email,
    normalize_phone,
    prepare_lead,
)


class WorkflowRepo:
    def __init__(self) -> None:
        self.contacts: dict[str, dict] = {}
        self.leads: list[dict] = []
        self.tasks: list[dict] = []
        self.metrics: list[int] = []

    def create_campaign(self, **values):
        return {"id": 41, **values}

    def find_contact_match(self, contact):
        return None

    def blacklist_match(self, **_values):
        return None

    def upsert_contacts(self, contacts, **_kwargs):
        for contact in contacts:
            self.contacts[contact["linkedin_url"]] = {"id": 7, **contact}
        return len(contacts), 0

    def get_contact_by_linkedin_url(self, linkedin_url):
        return self.contacts.get(linkedin_url)

    def get_contact(self, contact_id):
        return next((contact for contact in self.contacts.values() if contact["id"] == contact_id), None)

    def upsert_lead_record(self, **values):
        lead = {"id": len(self.leads) + 1, **values}
        self.leads.append(lead)
        return lead

    def ensure_followup_task(self, **values):
        task = {"id": len(self.tasks) + 1, **values}
        self.tasks.append(task)
        return task

    def refresh_customer_profile_snapshot(self, contact_id):
        return {"contact_id": contact_id}

    def refresh_campaign_metrics(self, campaign_id):
        self.metrics.append(campaign_id)
        return {"campaign_id": campaign_id}


def test_prepare_lead_normalizes_and_explains_score() -> None:
    lead = prepare_lead(
        {
            "first_name": " Ada ",
            "last_name": "Lovelace",
            "email": " ADA@EXAMPLE.COM ",
            "phone": "+1 (415) 555-0101",
            "company_name": "Premium Watch Dealer",
            "website": "https://www.example.com/about",
            "job_title": "Founder",
            "industry": "Luxury retail",
            "location": "United States",
        }
    )

    assert lead["email"] == "ada@example.com"
    assert lead["phone"] == "+14155550101"
    assert lead["company_domain"] == "example.com"
    assert lead["linkedin_url"].startswith("urn:lead:")
    assert lead["identity_status"] == "review"
    assert lead["lead_score"] > 0
    assert lead["source_context"]["score_breakdown"]
    assert lead["source_context"]["score_reason"]
    assert lead["source_context"]["recommended_channel"] == "email"


def test_normalizers_and_dedupe_key_reject_invalid_values() -> None:
    assert normalize_email("masked***@example.com") is None
    assert normalize_phone("123") is None
    assert dedupe_key({"email": "Sales@Example.com"}) == "email:sales@example.com"


def test_next_task_tracks_contact_state() -> None:
    now = datetime(2026, 7, 19, 8, tzinfo=UTC)

    enrich = next_task_for_contact({"status": "new", "company_name": "A"}, now=now)
    review = next_task_for_contact(
        {"status": "enriched", "company_name": "B", "email": "b@example.com", "email_status": "valid"},
        now=now,
    )
    reply = next_task_for_contact({"status": "replied", "company_name": "C"}, now=now)
    stopped = next_task_for_contact({"status": "unsubscribed", "company_name": "D"}, now=now)

    assert enrich["task_type"] == "enrich_contact"
    assert review["task_type"] == "generate_draft"
    assert reply["task_type"] == "reply"
    assert reply["priority"] == "urgent"
    assert stopped is None


def test_ingest_creates_canonical_contact_lead_task_and_campaign_metric() -> None:
    repo = WorkflowRepo()
    service = LeadWorkflowService(repo)

    result = service.ingest_contacts(
        [{"company_name": "Example", "website": "example.com", "job_title": "Owner"}],
        user={"id": 3, "username": "sales"},
        source_type="csv_import",
        source_ref="sample.csv",
    )

    assert result["inserted"] == 1
    assert result["linked"] == 1
    assert result["tasks_created"] == 1
    assert result["campaign_id"] == 41
    assert repo.leads[0]["source_type"] == "csv_import"
    assert repo.leads[0]["raw_data"]["company_name"] == "Example"
    assert repo.tasks[0]["task_type"] == "enrich_contact"
    assert repo.metrics == [41]


def test_ingest_keeps_blacklisted_lead_but_does_not_create_sales_task() -> None:
    class BlockedRepo(WorkflowRepo):
        def blacklist_match(self, **_values):
            return {"reason": "previous unsubscribe"}

    repo = BlockedRepo()
    result = LeadWorkflowService(repo).ingest_contacts(
        [{"company_name": "Blocked", "website": "blocked.example", "email": "person@blocked.example"}],
        user={"id": 3, "username": "sales"},
        source_type="csv_import",
        source_ref="blocked.csv",
    )

    saved = next(iter(repo.contacts.values()))
    assert result["linked"] == 1
    assert result["tasks_created"] == 0
    assert saved["status"] == "unsubscribed"
    assert saved["disposition"] == "abandoned"
    assert saved["source_context"]["blocked_reason"] == "previous unsubscribe"
