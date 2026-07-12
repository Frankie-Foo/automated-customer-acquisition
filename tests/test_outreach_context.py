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
