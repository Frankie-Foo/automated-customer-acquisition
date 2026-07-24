from sales_automation.clients import LLMClient
from sales_automation.services.outreach import PersonalizedEmailService
import pytest


def test_llm_opener_fallback_uses_imported_account_context():
    opener = LLMClient("").opener(
        {
            "company_name": "Luxepolis",
            "job_title": "Founder",
            "source_context": {
                "seed_reason": "India luxury resale platform with certified pre-owned positioning",
                "seed_category": "second-hand luxury platform",
            },
        }
    )

    assert "Luxepolis" in opener
    assert "certified pre-owned" in opener


def test_personalized_fallback_draft_uses_imported_account_context():
    class Config:
        sender = {"name": "vertuMay"}
        apis = {}
        raw = {"llm": {}}

    class Repo:
        def get_contact(self, contact_id):
            return {
                "id": contact_id,
                "first_name": "Ada",
                "company_name": "Luxepolis",
                "job_title": "Founder",
                "source_context": {
                    "seed_reason": "curated luxury resale marketplace in India",
                    "seed_category": "second-hand luxury platform",
                },
            }

        def list_lifecycle_activities(self, contact_id, limit=5):
            return []

    draft = PersonalizedEmailService(Config(), Repo()).draft(1)

    assert draft["subject"] == "Possible Vertu channel fit for Luxepolis"
    assert "curated luxury resale marketplace in India" in draft["body"]
    assert "premium mobile and luxury technology brand" in draft["body"]
    assert "brief reply" in draft["body"]


def test_internal_import_notes_are_never_exposed_in_fallback_draft():
    internal_note = (
        "[马来西亚3C渠道调研] | 联系人:Amy Tan | 触达优先级:P0 | "
        "核实状态:LinkedIn+PR已核实 | CEO | 是否跟进:否 | 是否回复:否 | "
        "跟进进度: | 跟进人: | 客户来源:2026-07马来西亚渠道调研 "
        "Public JobStreet listings indicate DirectD may be building its retail team."
    )

    class Config:
        sender = {"name": "Ivan"}
        apis = {}
        raw = {"llm": {}}

    class Repo:
        def get_contact(self, contact_id):
            return {
                "id": contact_id,
                "first_name": "Amy",
                "company_name": "DirectD",
                "job_title": "CEO",
                "industry": "consumer electronics retail",
                "source_context": {
                    "seed_category": "3C retail",
                    "seed_location": "Malaysia",
                    "seed_reason": internal_note,
                },
            }

        def list_lifecycle_activities(self, contact_id, limit=5):
            return []

    draft = PersonalizedEmailService(Config(), Repo()).draft(1)

    assert "DirectD" in draft["body"]
    assert "3C retail" in draft["body"]
    assert "触达优先级" not in draft["body"]
    assert "核实状态" not in draft["body"]
    assert "客户来源" not in draft["body"]
    assert "Public JobStreet" not in draft["body"]


def test_personalized_send_requires_matching_approved_draft():
    class Config:
        sender = {"name": "vertuMay"}
        apis = {}
        raw = {}

    class Repo:
        def get_private_contact_for_user(self, contact_id, user):
            return {
                "id": contact_id,
                "first_name": "Ada",
                "email": "ada@example.com",
                "email_status": "valid",
                "job_title": "Founder",
                "company_name": "Example",
                "lead_score": 90,
            }

        def get_latest_email_draft(self, contact_id, user_id=None):
            return {"status": "draft", "subject": "Subject", "body": "Body"}

    with pytest.raises(ValueError, match="approved"):
        PersonalizedEmailService(Config(), Repo()).send(
            1,
            subject="Subject",
            body="Body",
            user={"id": 2, "role": "sales"},
        )
