from sales_automation.services import WebhookService, _extract_contact_id, _extract_event_type, _extract_message_id


def test_extract_contact_id_from_metadata():
    assert _extract_contact_id({"data": {"metadata": {"contact_id": "42"}}}) == 42


def test_extract_event_type_normalizes_bounce():
    assert _extract_event_type("resend", {"type": "email.bounced"}) == "bounced"


def test_extract_event_type_normalizes_delivered():
    assert _extract_event_type("resend", {"type": "email.delivered"}) == "delivered"


def test_extract_event_type_normalizes_complaint_and_failure():
    assert _extract_event_type("resend", {"type": "email.complained"}) == "complained"
    assert _extract_event_type("resend", {"type": "email.failed"}) == "failed"


def test_extract_message_id_from_resend_payload():
    assert _extract_message_id({"data": {"email": {"id": "abc123"}}}) == "abc123"


def test_webhook_falls_back_to_message_id_when_metadata_missing():
    class Repo:
        def __init__(self):
            self.events = []

        def find_contact_id_by_message_id(self, message_id):
            return 42 if message_id == "email_123" else None

        def record_event(self, contact_id, event_type, payload):
            self.events.append((contact_id, event_type, payload))

    repo = Repo()
    event = WebhookService(repo).process_payload("resend", {"type": "email.delivered", "data": {"id": "email_123"}})
    assert event == "delivered"
    assert repo.events == [(42, "delivered", {"type": "email.delivered", "data": {"id": "email_123"}})]

