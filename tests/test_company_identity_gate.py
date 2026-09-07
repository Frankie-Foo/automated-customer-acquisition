from pathlib import Path
from types import SimpleNamespace
import os

import psycopg
import pytest
from psycopg.types.json import Jsonb

from sales_automation.config import AppConfig
from sales_automation.db import Repository, _with_customer_intelligence
from sales_automation.outbound_quality import assess_icp, enrichment_readiness
from sales_automation.outreach_guard import company_identity_issues, send_readiness
from sales_automation.services.enrichment import EnrichmentService
from sales_automation.services.outreach import PersonalizedEmailService


def contact(**overrides):
    return {
        "id": 1, "first_name": "Amy", "last_name": "Tan", "job_title": "Managing Director",
        "company_name": "Premium Retail Group", "company_domain": "premium.example",
        "industry": "luxury retail", "location": "Malaysia", "email": "amy@premium.example",
        "email_status": "valid", "lead_score": 100, "icp_assessment": {"score": 100},
        "identity_confidence": 95, "linkedin_url": "https://linkedin.com/in/amy",
        **overrides,
    }


@pytest.mark.parametrize("name", ["LinkedIn", " leadiq ", "PROFESSIONAL PROFILE", "Entrepreneur", "unknown", "N/A"])
def test_platform_and_placeholder_company_override_stale_high_scores(name):
    row = contact(company_name=name)
    assert company_identity_issues(row) == ["company_identity_needs_review"]
    assert not assess_icp(row)["qualified"]
    assert not enrichment_readiness(row)["ok"]
    assert not enrichment_readiness(row, paid=True)["ok"]
    for strict in (False, True):
        assert "company_identity_needs_review" in send_readiness(row, strict_automation=strict)["reasons"]


def test_missing_name_and_domain_rejected_but_domain_only_can_be_enriched():
    row = contact(company_name=" \t", company_domain="\n")
    assert company_identity_issues(row) == ["missing_company_identity"]
    assert not enrichment_readiness(row)["ok"]
    assert not send_readiness(row)["ok"]
    row["company_domain"] = "premium.example"
    assert company_identity_issues(row) == []
    assert enrichment_readiness(row)["ok"]


@pytest.mark.parametrize("name", ["The Hour Glass", "LinkedIn Retail Partners Ltd", "Entrepreneur Watches", "DJI"])
def test_normal_company_names_are_not_substring_blocked(name):
    row = contact(company_name=name)
    assert not company_identity_issues(row)
    assert send_readiness(row)["ok"]


def test_read_only_response_recomputes_company_issues_without_mutating_record():
    row = contact(company_name="LinkedIn")
    result = _with_customer_intelligence(row)
    assert result["company_identity_issues"] == ["company_identity_needs_review"]
    assert "company_identity_issues" not in row
    corrected = {**result, "company_name": "Premium Retail Group"}
    assert _with_customer_intelligence(corrected)["company_identity_issues"] == []


@pytest.mark.parametrize("mode", ["ai", "custom"])
def test_bad_company_cannot_generate_or_save_a_draft(mode):
    repo = SimpleNamespace(get_contact=lambda _id: contact(company_name="LinkedIn"))
    service = PersonalizedEmailService(AppConfig(raw={}, root_dir=Path(".")), repo)
    with pytest.raises(ValueError, match="Company needs review"):
        service.draft(1, mode=mode)


def test_manual_email_enrichment_does_not_call_provider_or_save_invalid_company(monkeypatch):
    repo = SimpleNamespace(get_contact=lambda _id: contact(company_name="LeadIQ"))
    service = EnrichmentService(AppConfig(raw={}, root_dir=Path(".")), repo)
    monkeypatch.setattr(service, "_enrich_one", lambda *a: pytest.fail("must not call provider"))
    with pytest.raises(ValueError, match="Company needs review"):
        service.enrich_contact(1)


def test_company_review_policy_is_included_in_list_filters():
    repo = Repository(None)
    review = []
    ready = []
    repo._append_contact_filter(review, "needs_review")
    repo._append_contact_filter(ready, "ready_to_send")
    assert "'linkedin'" in review[0]
    assert " OR " in review[0]
    assert "AND NOT" in ready[0]
    assert "'leadiq'" in ready[0]


@pytest.mark.skipif(not os.getenv("SALESBOT_QA_PG_DSN"), reason="isolated QA PostgreSQL not configured")
def test_company_filters_execute_in_postgres():
    repo = Repository(None)
    with psycopg.connect(os.environ["SALESBOT_QA_PG_DSN"]) as conn:
        for name, domain, rejected in [
            ("LinkedIn", "linkedin.com", True), (" \tLeadIQ\n", "example.com", True),
            ("", "", True), ("", "retail.example", False),
            ("Premium Retail", "retail.example", False),
        ]:
            row = contact(company_name=name, company_domain=domain)
            row.update(status="enriched", email_candidates=[], identity_status="verified", enrich_error=None)
            for key in ("needs_review", "ready_to_send", "auto_enrich"):
                clauses = []
                repo._append_contact_filter(clauses, key)
                row["email_status"] = "missing" if key == "auto_enrich" else "valid"
                result = conn.execute(
                    "SELECT COALESCE((" + clauses[0] + "), false) "
                    "FROM jsonb_populate_record(NULL::public.contacts, %s) c", (Jsonb(row),)
                ).fetchone()[0]
                assert result == (rejected if key == "needs_review" else not rejected), (name, key)
