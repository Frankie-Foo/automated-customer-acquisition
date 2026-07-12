from types import SimpleNamespace

from sales_automation.services import WebhookService, _extract_contact_id, _extract_event_type, _extract_message_id, _extract_recipient_email, _extract_sender_email


def test_extract_contact_id_from_metadata():
    assert _extract_contact_id({"data": {"metadata": {"contact_id": "42"}}}) == 42


def test_extract_event_type_normalizes_bounce():
    assert _extract_event_type("resend", {"type": "email.bounced"}) == "bounced"


def test_extract_event_type_normalizes_delivered():
    assert _extract_event_type("resend", {"type": "email.delivered"}) == "delivered"


def test_extract_event_type_normalizes_inbound_received_as_reply():
    assert _extract_event_type("resend", {"type": "email.received"}) == "replied"


def test_extract_event_type_normalizes_complaint_and_failure():
    assert _extract_event_type("resend", {"type": "email.complained"}) == "complained"
    assert _extract_event_type("resend", {"type": "email.failed"}) == "failed"


def test_extract_message_id_from_resend_payload():
    assert _extract_message_id({"data": {"email": {"id": "abc123"}}}) == "abc123"


def test_extract_recipient_email_from_resend_payload():
    assert _extract_recipient_email({"data": {"to": ["Person <lead@example.com>"]}}) == "lead@example.com"
    assert _extract_recipient_email({"data": {"to": [{"email": "lead@example.com"}]}}) == "lead@example.com"


def test_extract_sender_email_from_inbound_payload():
    assert _extract_sender_email({"data": {"from": "Lead <lead@example.com>"}}) == "lead@example.com"


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


def test_webhook_falls_back_to_recipient_email_when_message_id_missing():
    class Repo:
        def __init__(self):
            self.events = []

        def find_contact_id_by_message_id(self, message_id):
            return None

        def find_contact_id_by_email(self, email):
            return 77 if email == "lead@example.com" else None

        def record_event(self, contact_id, event_type, payload):
            self.events.append((contact_id, event_type, payload))

    repo = Repo()
    payload = {"type": "email.opened", "data": {"to": [{"email": "lead@example.com"}]}}
    event = WebhookService(repo).process_payload("resend", payload)
    assert event == "opened"
    assert repo.events == [(77, "opened", payload)]


def test_resend_received_webhook_fetches_reply_body_before_recording():
    class Repo:
        def __init__(self):
            self.events = []
            self.activities = []

        def find_contact_id_by_message_id(self, message_id):
            return None

        def find_contact_id_by_email(self, email):
            return 77 if email == "lead@example.com" else None

        def record_event(self, contact_id, event_type, payload):
            self.events.append((contact_id, event_type, payload))

        def add_lifecycle_activity(self, contact_id, **kwargs):
            self.activities.append((contact_id, kwargs))

    class Http:
        def request(self, method, url, **kwargs):
            assert method == "GET"
            assert url.endswith("/emails/receiving/inbound-123")
            assert kwargs["headers"]["Authorization"] == "Bearer re_test"
            return {"text": "Please send the price list.", "message_id": "<reply-123@example.com>"}

    repo = Repo()
    config = SimpleNamespace(raw={}, apis={"resend_key": "re_test"})
    event = WebhookService(repo, config=config, http=Http()).process_payload(
        "resend",
        {
            "type": "email.received",
            "data": {
                "email_id": "inbound-123",
                "from": "lead@example.com",
                "to": ["reply@example.com"],
                "subject": "Re: Vertu partnership",
            },
        },
    )

    assert event == "replied"
    assert repo.events[0][2]["data"]["text"] == "Please send the price list."
    assert repo.activities[0][1]["content"] == "Please send the price list."

