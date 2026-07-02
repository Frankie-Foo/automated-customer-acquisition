import pytest

from sales_automation.outreach_guard import (
    annotate_delivery_payload,
    classify_delivery_failure,
    is_sendable_email,
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
