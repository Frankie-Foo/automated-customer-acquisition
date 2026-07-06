import pytest

from sales_automation.outreach_guard import (
    annotate_delivery_payload,
    classify_delivery_failure,
    is_sendable_email,
    lead_quality_score,
    send_readiness,
    send_delay_seconds,
    validate_email_body,
)


def test_sendable_email_rejects_role_based_and_masked_addresses():
    assert is_sendable_email("darwin.lee@rolex.com")
    assert not is_sendable_email("info@rolex.com")
    assert not is_sendable_email("d******@rolex.com")
    assert not is_sendable_email("not-an-email")


def test_validate_email_body_rejects_truncated_or_unresolved_content():
    with pytest.raises(ValueError):
        validate_email_body("Subject", "Hi Frank,", min_chars=30)
    with pytest.raises(ValueError):
        validate_email_body("Subject", "Hi [Name], this is a long enough message body for testing.", min_chars=30)


def test_delivery_failure_classification_and_annotation():
    payload = {"type": "email.bounced", "data": {"reason": "Address not found"}}
    assert classify_delivery_failure("bounced", payload) == "bounced: address_not_found"
    assert annotate_delivery_payload("bounced", payload)["delivery_reason"] == "bounced: address_not_found"


def test_send_delay_defaults_to_zero_without_config():
    class Config:
        raw = {}

    assert send_delay_seconds(Config()) == 0


def test_send_readiness_accepts_verified_decision_maker():
    contact = {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "job_title": "Founder",
        "company_name": "Example Inc",
        "company_domain": "example.com",
        "email": "ada.lovelace@example.com",
        "email_status": "valid",
        "email_confidence": 90,
        "lead_score": 78,
    }

    readiness = send_readiness(contact)

    assert readiness["ok"]
    assert readiness["tier"] == "sendable"
    assert readiness["score"] == 78
    assert readiness["reasons"] == []


def test_send_readiness_blocks_role_based_and_low_value_contacts():
    contact = {
        "first_name": "Support",
        "job_title": "Customer Service Assistant",
        "company_name": "Example Inc",
        "email": "info@example.com",
        "email_status": "valid",
        "lead_score": 80,
    }

    readiness = send_readiness(contact)

    assert not readiness["ok"]
    assert "email_not_personal_work" in readiness["reasons"]
    assert "low_value_title" in readiness["reasons"]


def test_send_readiness_blocks_low_score_even_with_valid_email():
    contact = {
        "first_name": "Sam",
        "job_title": "Retail Director",
        "company_name": "Example Inc",
        "email": "sam@example.com",
        "email_status": "valid",
        "lead_score": 35,
    }

    readiness = send_readiness(contact)

    assert not readiness["ok"]
    assert "lead_score_below_threshold" in readiness["reasons"]


def test_lead_quality_score_can_be_inferred_from_contact_context():
    contact = {
        "first_name": "Ada",
        "job_title": "Managing Director",
        "company_domain": "example.com",
        "email": "ada@example.com",
        "email_status": "valid",
        "industry": "luxury retail",
        "source_context": {"seed_reason": "premium retail fit"},
    }

    assert lead_quality_score(contact) >= 80
