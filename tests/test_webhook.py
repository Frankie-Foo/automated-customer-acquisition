from sales_automation.services import _extract_contact_id, _extract_event_type


def test_extract_contact_id_from_metadata():
    assert _extract_contact_id({"data": {"metadata": {"contact_id": "42"}}}) == 42


def test_extract_event_type_normalizes_bounce():
    assert _extract_event_type("resend", {"type": "email.bounced"}) == "bounced"

