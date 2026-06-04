from sales_automation.status import can_transition, next_sent_status, validate_status


def test_state_machine_allows_expected_flow():
    assert can_transition("new", "enriched")
    assert can_transition("sent_1", "replied")
    assert not can_transition("new", "sent_1")


def test_manual_override_allows_any_valid_status():
    assert can_transition("new", "replied", manual=True)


def test_next_sent_status():
    assert next_sent_status(2) == "sent_2"


def test_validate_rejects_unknown():
    try:
        validate_status("bad")
    except ValueError as exc:
        assert "Unknown" in str(exc)
    else:
        raise AssertionError("validate_status should reject invalid status")

