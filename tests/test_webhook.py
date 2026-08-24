from pathlib import Path
from types import SimpleNamespace

from sales_automation.services import WebhookService, _extract_contact_id, _extract_event_type, _extract_message_id, _extract_recipient_email, _extract_sender_email
from sales_automation.services.webhooks import _furthest_lifecycle_stage


def test_auto_reply_event_is_supported_by_database_schema():
    sql = Path("migrations/046_auto_reply_email_event.sql").read_text(encoding="utf-8")
    assert "ALTER TYPE email_event_type ADD VALUE IF NOT EXISTS 'auto_reply'" in sql


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


def test_extract_event_type_normalizes_sent_and_rejects_unknown_events():
    assert _extract_event_type("resend", {"type": "email.sent"}) == "sent"
    assert _extract_event_type("resend", {"type": "email.some_future_event"}) == "unknown"


def test_provider_sent_webhook_is_idempotent_after_local_send_recording():
    class Repo:
        def record_event(self, *args, **kwargs):
            raise AssertionError("provider sent webhook must not insert another sent event")

    assert WebhookService(Repo()).process_payload("resend", {"type": "email.sent"}) == "sent"


def test_extract_message_id_from_resend_payload():
    assert _extract_message_id({"data": {"email": {"id": "abc123"}}}) == "abc123"


def test_extract_recipient_email_from_resend_payload():
    assert _extract_recipient_email({"data": {"to": ["Person <lead@example.com>"]}}) == "lead@example.com"
    assert _extract_recipient_email({"data": {"to": [{"email": "lead@example.com"}]}}) == "lead@example.com"


def test_extract_sender_email_from_inbound_payload():
    assert _extract_sender_email({"data": {"from": "Lead <lead@example.com>"}}) == "lead@example.com"


def test_automated_reply_progress_never_downgrades_lifecycle():
    assert _furthest_lifecycle_stage("meeting", "replied") == "meeting"
    assert _furthest_lifecycle_stage("replied", "meeting") == "meeting"


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
            self.lifecycle_updates = []

        def find_contact_id_by_message_id(self, message_id):
            return None

        def find_contact_id_by_email(self, email):
            return 77 if email == "lead@example.com" else None

        def record_event(self, contact_id, event_type, payload):
            self.events.append((contact_id, event_type, payload))

        def add_lifecycle_activity(self, contact_id, **kwargs):
            self.activities.append((contact_id, kwargs))

        def update_lifecycle(self, contact_id, **kwargs):
            self.lifecycle_updates.append((contact_id, kwargs))

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
    assert repo.lifecycle_updates == [(77, {"lifecycle_stage": "replied", "disposition": "active"})]


def test_confirmed_meeting_reply_advances_lifecycle_and_activity():
    class Repo:
        def __init__(self):
            self.events = []
            self.lifecycle_updates = []
            self.activities = []

        def find_contact_id_by_email(self, email):
            return 77 if email == "lead@example.com" else None

        def route_inbound_reply(self, contact_id, user_id):
            return {"owner_user_id": 3, "reply_assignment_pending": False}

        def record_event(self, contact_id, event_type, payload):
            self.events.append((contact_id, event_type, payload))

        def get_contact(self, contact_id):
            return {
                "id": contact_id,
                "owner_user_id": 3,
                "status": "replied",
                "lifecycle_stage": "replied",
                "sabcd_stage": "C",
            }

        def update_lifecycle(self, contact_id, **kwargs):
            self.lifecycle_updates.append((contact_id, kwargs))

        def close_open_followup_tasks(self, contact_id):
            pass

        def add_lifecycle_activity(self, contact_id, **kwargs):
            self.activities.append((contact_id, kwargs))

        def record_interaction(self, **kwargs):
            pass

    repo = Repo()
    event = WebhookService(repo).process_payload(
        "imap",
        {
            "event_type": "replied",
            "from": "lead@example.com",
            "subject": "Re: Vertu",
            "text": "Looking forward to our call on Monday. Please send the meeting details.",
        },
    )

    assert event == "replied"
    assert repo.lifecycle_updates == [(77, {"lifecycle_stage": "meeting", "disposition": "active"})]
    assert repo.activities[0][1]["lifecycle_stage"] == "meeting"


def test_explicit_negative_reply_abandons_without_advancing_lifecycle():
    class Repo:
        def __init__(self):
            self.lifecycle_updates = []
            self.activities = []

        def find_contact_id_by_email(self, email):
            return 77 if email == "lead@example.com" else None

        def route_inbound_reply(self, contact_id, user_id):
            return {"owner_user_id": 3, "reply_assignment_pending": False}

        def record_event(self, contact_id, event_type, payload):
            pass

        def get_contact(self, contact_id):
            return {
                "id": contact_id,
                "owner_user_id": 3,
                "pool_type": "private",
                "status": "sent_1",
                "lifecycle_stage": "lead",
                "disposition": "abandoned",
            }

        def update_lifecycle(self, contact_id, **kwargs):
            self.lifecycle_updates.append((contact_id, kwargs))

        def close_open_followup_tasks(self, contact_id):
            pass

        def add_lifecycle_activity(self, contact_id, **kwargs):
            self.activities.append((contact_id, kwargs))

        def record_interaction(self, **kwargs):
            pass

    repo = Repo()
    WebhookService(repo).process_payload(
        "imap",
        {
            "event_type": "replied",
            "from": "lead@example.com",
            "subject": "Re: Vertu",
            "text": "Thank you, but this is not a fit for our business.",
        },
    )

    assert repo.lifecycle_updates == [(77, {"lifecycle_stage": "lead", "disposition": "abandoned"})]
    assert repo.activities[0][1]["lifecycle_stage"] == "lead"


def test_not_now_reply_waits_without_advancing_lifecycle():
    class Repo:
        def __init__(self):
            self.lifecycle_updates = []

        def find_contact_id_by_email(self, email):
            return 77 if email == "lead@example.com" else None

        def route_inbound_reply(self, contact_id, user_id):
            return {"owner_user_id": 3, "reply_assignment_pending": False}

        def record_event(self, contact_id, event_type, payload):
            pass

        def get_contact(self, contact_id):
            return {
                "id": contact_id,
                "owner_user_id": 3,
                "status": "sent_1",
                "lifecycle_stage": "lead",
            }

        def update_lifecycle(self, contact_id, **kwargs):
            self.lifecycle_updates.append((contact_id, kwargs))

        def close_open_followup_tasks(self, contact_id):
            pass

        def add_lifecycle_activity(self, contact_id, **kwargs):
            pass

        def record_interaction(self, **kwargs):
            pass

    repo = Repo()
    WebhookService(repo).process_payload(
        "imap",
        {
            "event_type": "replied",
            "from": "lead@example.com",
            "subject": "Re: Vertu",
            "text": "Not now. Please circle back next quarter.",
        },
    )

    assert repo.lifecycle_updates == [(77, {"lifecycle_stage": "lead", "disposition": "waiting"})]


def test_reply_uses_in_reply_to_before_sender_email_and_updates_original_message():
    class Repo:
        def __init__(self):
            self.events = []
            self.outbound_updates = []

        def find_contact_id_by_message_id(self, message_id):
            return 42 if message_id == "<outbound-42@example.com>" else None

        def find_contact_id_by_email(self, email):
            return 77

        def record_event(self, contact_id, event_type, payload):
            self.events.append((contact_id, event_type, payload))

        def update_outreach_message_event(self, **kwargs):
            self.outbound_updates.append(kwargs)

    repo = Repo()

    event = WebhookService(repo).process_payload(
        "imap",
        {
            "event_type": "replied",
            "from": "lead@example.com",
            "message_id": "<reply-99@example.com>",
            "in_reply_to": "<outbound-42@example.com>",
            "subject": "Re: Vertu",
            "text": "Please send more information.",
        },
    )

    assert event == "replied"
    assert repo.events[0][0] == 42
    assert repo.outbound_updates == [
        {
            "provider": "imap",
            "provider_message_id": "<outbound-42@example.com>",
            "event_type": "replied",
            "error": None,
        }
    ]


def test_out_of_office_reply_keeps_existing_followups_and_is_not_counted_as_human_reply():
    class Repo:
        def __init__(self):
            self.events = []
            self.closed = []
            self.activities = []

        def find_contact_id_by_message_id(self, message_id):
            return None

        def find_contact_id_by_email(self, email):
            return 77 if email == "lead@example.com" else None

        def route_inbound_reply(self, contact_id, user_id):
            return {"owner_user_id": 3, "reply_assignment_pending": False}

        def record_event(self, contact_id, event_type, payload):
            self.events.append((contact_id, event_type, payload))

        def get_contact(self, contact_id):
            return {"id": contact_id, "owner_user_id": 3, "status": "replied", "sabcd_stage": "D"}

        def close_open_followup_tasks(self, contact_id):
            self.closed.append(contact_id)

        def record_interaction(self, **kwargs):
            pass

        def add_lifecycle_activity(self, contact_id, **kwargs):
            self.activities.append((contact_id, kwargs))

    repo = Repo()
    payload = {
        "type": "email.received",
        "data": {
            "from": "lead@example.com",
            "subject": "Automatic reply",
            "text": "I am out of office until Monday.",
        },
    }

    event = WebhookService(repo).process_payload("resend", payload)

    assert event == "replied"
    assert repo.closed == []
    assert repo.events[0][1] == "auto_reply"
    assert repo.events[0][2]["reply_classification"]["label"] == "ooo"
    assert repo.activities == []


def test_unclassified_human_reply_enters_reply_lifecycle_and_gets_next_task(monkeypatch):
    class Repo:
        def __init__(self):
            self.events = []
            self.closed = []
            self.lifecycle_updates = []
            self.activities = []

        def find_contact_id_by_email(self, email):
            return 77 if email == "lead@example.com" else None

        def route_inbound_reply(self, contact_id, user_id):
            return {"owner_user_id": 3, "reply_assignment_pending": False}

        def record_event(self, contact_id, event_type, payload):
            self.events.append((contact_id, event_type, payload))

        def get_contact(self, contact_id):
            return {"id": contact_id, "owner_user_id": 3, "status": "replied", "lifecycle_stage": "lead"}

        def update_lifecycle(self, contact_id, **kwargs):
            self.lifecycle_updates.append((contact_id, kwargs))

        def close_open_followup_tasks(self, contact_id):
            self.closed.append(contact_id)

        def add_lifecycle_activity(self, contact_id, **kwargs):
            self.activities.append((contact_id, kwargs))

        def record_interaction(self, **kwargs):
            pass

    class Workflow:
        calls = []

        def __init__(self, repo):
            self.repo = repo

        def ensure_next_task(self, contact_id, owner_user_id=None):
            self.calls.append((contact_id, owner_user_id))

    repo = Repo()
    from sales_automation.services import webhooks

    monkeypatch.setattr(webhooks, "LeadWorkflowService", Workflow)
    event = WebhookService(repo).process_payload(
        "imap",
        {
            "event_type": "replied",
            "from": "lead@example.com",
            "subject": "Re: Vertu",
            "text": "Thanks, I will review this internally and come back to you.",
        },
    )

    assert event == "replied"
    assert repo.events[0][1] == "replied"
    assert repo.lifecycle_updates == [(77, {"lifecycle_stage": "replied", "disposition": "active"})]
    assert repo.closed == [77]
    assert Workflow.calls == [(77, 3)]
    assert repo.activities[0][1]["lifecycle_stage"] == "replied"

