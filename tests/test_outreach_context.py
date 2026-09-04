from sales_automation.clients import LLMClient
import json

import sales_automation.services.outreach as outreach_module
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


def test_legacy_llm_client_never_calls_provider():
    class Http:
        def request(self, *_args, **_kwargs):
            raise AssertionError("legacy client must not call an LLM provider")

    opener = LLMClient("configured-key", http=Http()).opener(
        {"company_name": "Example", "job_title": "Founder", "industry": "luxury"}
    )
    assert "Example" in opener


def test_ai_draft_with_wrong_recipient_falls_back_to_grounded_copy(monkeypatch):
    wrong_body = (
        "Hi Bob,\n\nI work with VERTU's international channel development team. Another Retail operates an "
        "established luxury customer channel that may be relevant to a selective partnership assessment. The commercial "
        "question is whether VERTU can complement its portfolio and service model without creating operational distraction. "
        "Two routes could be a controlled shop-in-shop format or selective local distribution. Any option would require "
        "validation against customer fit, product mix, location economics and operating responsibilities. This is a working "
        "hypothesis rather than an assumed outcome, and the purpose is to establish whether there is enough strategic fit "
        "to continue. Would a concise market-specific partnership discussion be useful?"
    )

    class Gateway:
        def __init__(self, *_args, **_kwargs):
            pass

        def can_generate(self, _contact):
            return True

        def complete(self, **_kwargs):
            return json.dumps({"subject": "VERTU x Another Retail", "body": wrong_body})

    class Config:
        sender = {"name": "April"}
        apis = {}
        raw = {"llm": {}}

    class Repo:
        def get_contact(self, contact_id):
            return {
                "id": contact_id,
                "first_name": "Amy",
                "company_name": "Premium Retail",
                "job_title": "Founder",
                "industry": "luxury retail",
                "location": "Singapore",
            }

        def get_contact_research(self, _contact_id):
            return {}

        def list_lifecycle_activities(self, contact_id, limit=5):
            return []

    monkeypatch.setattr(outreach_module, "LLMGateway", Gateway)

    draft = PersonalizedEmailService(Config(), Repo()).draft(1)

    assert draft["subject"] == "Strategic Vision: VERTU × Premium Retail — Singapore Partnership Roadmap"
    assert draft["body"].startswith("Hi Amy,")
    assert "Another Retail" not in draft["body"]


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

    assert draft["subject"] == "Strategic Vision: VERTU × Luxepolis — India Partnership Roadmap"
    assert "curated luxury resale marketplace in India" in draft["body"]
    assert "international channel development team" in draft["body"]
    assert "two practical routes" in draft["body"]
    assert "formal partnership roadmap" in draft["body"]


def test_luxury_group_draft_uses_portfolio_partnership_structure():
    class Config:
        sender = {"name": "April"}
        apis = {}
        raw = {"llm": {}}

    class Repo:
        def get_contact(self, contact_id):
            return {
                "id": contact_id,
                "first_name": "Manu",
                "company_name": "Reliance Luxury",
                "job_title": "General Director",
                "industry": "luxury retail",
                "location": "India",
            }

        def list_lifecycle_activities(self, contact_id, limit=5):
            return []

    draft = PersonalizedEmailService(Config(), Repo()).draft(1)

    assert draft["subject"] == "Strategic Vision: VERTU × Reliance Luxury — India Partnership Roadmap"
    assert "selective adjacent category" in draft["body"]
    assert "formal partnership roadmap" in draft["body"]
    assert "Head of CIS & South Asia" in draft["body"]
    assert "VERTU is a British luxury technology brand" in draft["body"]
    assert "VERTU is ready to return to India" in draft["body"]
    assert "- Luxury smartphones" in draft["body"]
    assert "- Luxury SUV" in draft["body"]
    assert "- Monthly new AIoT products" in draft["body"]
    assert "$30" not in draft["body"]


def test_automotive_group_is_detected_from_company_name():
    class Config:
        sender = {"name": "April"}
        apis = {}
        raw = {"llm": {}}

    class Repo:
        def get_contact(self, contact_id):
            return {
                "id": contact_id,
                "first_name": "Rajiv",
                "company_name": "Group Landmark / Landmark Cars",
                "job_title": "Whole-Time Director",
                "location": "India",
            }

        def list_lifecycle_activities(self, contact_id, limit=5):
            return []

    draft = PersonalizedEmailService(Config(), Repo()).draft(1)

    assert draft["subject"] == "VERTU × Group Landmark / Landmark Cars — Luxury Tech + Automotive Synergy"
    assert "including automotive-related directions" in draft["body"]


def test_company_automotive_signal_wins_over_generic_luxury_industry():
    class Config:
        sender = {"name": "April"}
        apis = {}
        raw = {"llm": {}}

    class Repo:
        def get_contact(self, contact_id):
            return {
                "id": contact_id,
                "first_name": "Rajiv",
                "company_name": "Landmark Cars",
                "job_title": "Director",
                "industry": "luxury retail",
                "country": "India",
            }

        def list_lifecycle_activities(self, contact_id, limit=5):
            return []

    draft = PersonalizedEmailService(Config(), Repo()).draft(1)

    assert draft["subject"] == "VERTU × Landmark Cars — Luxury Tech + Automotive Synergy"


def test_automotive_template_is_selected_from_contact_research():
    class Config:
        sender = {"name": "April"}
        apis = {}
        raw = {"llm": {}}

    class Repo:
        def get_contact(self, contact_id):
            return {
                "id": contact_id,
                "first_name": "Anna",
                "company_name": "Example Holdings",
                "job_title": "Managing Director",
                "country": "Russia",
            }

        def get_contact_research(self, contact_id):
            return {
                "summary": "The group operates Porsche and Bentley dealerships for affluent customers.",
                "sources": [{"title": "Official automotive network", "snippet": "Premium car dealer network."}],
            }

        def list_lifecycle_activities(self, contact_id, limit=5):
            return []

    draft = PersonalizedEmailService(Config(), Repo()).draft(1)

    assert draft["subject"] == "VERTU × Example Holdings — Luxury Tech + Automotive Synergy"
    assert "automotive-related directions" in draft["body"]


def test_luxury_template_is_selected_from_contact_research():
    class Config:
        sender = {"name": "April"}
        apis = {}
        raw = {"llm": {}}

    class Repo:
        def get_contact(self, contact_id):
            return {
                "id": contact_id,
                "first_name": "Elena",
                "company_name": "Example Holdings",
                "job_title": "General Director",
                "country": "Russia",
            }

        def get_contact_research(self, contact_id):
            return {
                "summary": "The company operates luxury jewelry and watch boutiques.",
                "sources": [{"title": "Official boutique portfolio", "snippet": "Fine jewelry and diamond retail."}],
            }

        def list_lifecycle_activities(self, contact_id, limit=5):
            return []

    draft = PersonalizedEmailService(Config(), Repo()).draft(1)

    assert draft["subject"] == "Strategic Vision: VERTU × Example Holdings — Russia Partnership Roadmap"
    assert "selective adjacent category" in draft["body"]


def test_market_intent_uses_contact_country_instead_of_hardcoded_india():
    class Config:
        sender = {"name": "April"}
        apis = {}
        raw = {"llm": {}}

    class Repo:
        def get_contact(self, contact_id):
            return {
                "id": contact_id,
                "first_name": "Deniz",
                "company_name": "Istanbul Luxury Group",
                "job_title": "CEO",
                "industry": "luxury retail",
                "country": "Turkey",
            }

        def list_lifecycle_activities(self, contact_id, limit=5):
            return []

    draft = PersonalizedEmailService(Config(), Repo()).draft(1)

    assert "ready to expand in Türkiye" in draft["body"]
    assert "return to India" not in draft["body"]
    assert "Türkiye Partnership Roadmap" in draft["subject"]


def test_personalized_fallback_changes_argument_for_recipient_role():
    class Config:
        sender = {"name": "April"}
        apis = {}
        raw = {"llm": {}}

    class Repo:
        def __init__(self, title):
            self.title = title

        def get_contact(self, contact_id):
            return {
                "id": contact_id,
                "first_name": "Ada",
                "company_name": "Premium Group",
                "job_title": self.title,
                "industry": "luxury retail",
            }

        def list_lifecycle_activities(self, contact_id, limit=5):
            return []

    buyer = PersonalizedEmailService(Config(), Repo("Jewellery Buyer")).draft(1)
    marketer = PersonalizedEmailService(Config(), Repo("Marketing Director")).draft(1)

    assert buyer["subject"] != marketer["subject"]
    assert "assortment, customer fit" in buyer["body"]
    assert "VIP engagement" in marketer["body"]


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
