from sales_automation.db import Repository
from sales_automation.services import outreach
from sales_automation.services.outreach import PersonalizedEmailService
from sales_automation.services.webhooks import WebhookService
from types import SimpleNamespace
from contextlib import contextmanager


def test_email_draft_work_queue_filters_are_explicit_and_private():
    repo = Repository(None)

    expected = {
        "missing_draft": "draft.id IS NULL",
        "draft_pending": "draft.status = 'draft'",
        "draft_approved": "draft.status = 'approved'",
    }

    for filter_key, fragment in expected.items():
        clauses = []
        repo._append_contact_filter(clauses, filter_key)
        assert len(clauses) == 1
        assert "c.pool_type = 'private'" in clauses[0]
        assert fragment in clauses[0]


def test_contact_list_applies_customer_intelligence_fallback():
    class Cursor:
        def fetchall(self):
            return [{
                "id": 1,
                "first_name": "Ada",
                "job_title": "Founder",
                "company_name": "Example",
                "industry": "luxury retail",
                "profile_summary": None,
                "profile_insights": {},
            }]

    class Connection:
        def execute(self, query, params):
            return Cursor()

    class Database:
        @contextmanager
        def connect(self):
            yield Connection()

    rows = Repository(Database()).list_contacts(user={"role": "admin"}, limit=10)

    assert rows[0]["profile_summary"]
    assert rows[0]["profile_insights"]["icp_fit_score"] >= 0


def test_approved_send_to_reply_advances_customer_lifecycle(monkeypatch):
    subject = "A practical channel idea for Example"
    body = (
        "I noticed Example is expanding its premium retail portfolio. "
        "Vertu may fit customers who value differentiated products and service. "
        "Would a brief channel discussion be relevant this quarter?"
    )

    class Repo:
        def __init__(self):
            self.contact = {
                "id": 9,
                "first_name": "Ada",
                "last_name": "Lee",
                "email": "ada.lee@example.com",
                "email_status": "valid",
                "email_confidence": 95,
                "job_title": "Founder",
                "company_name": "Example",
                "company_domain": "example.com",
                "lead_score": 90,
                "sequence_step": 0,
                "status": "enriched",
                "sabcd_stage": "D",
                "lifecycle_stage": "lead",
            }
            self.draft = {"status": "approved", "subject": subject, "body": body}
            self.activities = []

        def get_private_contact_for_user(self, contact_id, user):
            return dict(self.contact) if contact_id == 9 and user["id"] == 3 else None

        def get_latest_email_draft(self, contact_id, user_id=None):
            return dict(self.draft)

        def reserve_send_attempt(self, *args, **kwargs):
            return {"reserved": True}

        def finish_send_attempt(self, *args, **kwargs):
            pass

        def record_manual_sent(self, contact_id, step, email_subject, message_id, metadata):
            self.contact.update(status=f"sent_{step}", sequence_step=step, sabcd_stage="C")
            return True

        def mark_latest_email_draft_sent(self, contact_id, user_id=None):
            self.draft["status"] = "sent"

        def record_event(self, contact_id, event_type, payload):
            if event_type == "replied":
                self.contact.update(status="replied", lifecycle_stage="replied", sabcd_stage="C")

        def add_lifecycle_activity(self, contact_id, **kwargs):
            self.activities.append(kwargs)

    class SenderPool:
        def __init__(self, config, repo):
            pass

        def pick_sender(self):
            return {"id": 1, "provider": "resend", "name": "VERTU", "email": "sales@mail.example.com", "dry_run": False}

        def record_send(self, sender):
            pass

    class Mailer:
        def __init__(self, *args, **kwargs):
            pass

        def send(self, *args, **kwargs):
            return "message-9"

    monkeypatch.setattr(outreach, "SenderPoolManager", SenderPool)
    monkeypatch.setattr(outreach, "MailClient", Mailer)
    config = SimpleNamespace(
        sender={"name": "VERTU", "email": "sales@mail.example.com", "provider": "resend"},
        apis={"resend_key": "test"},
        raw={"app": {"public_base_url": "https://sales.example.com", "tracking_signing_secret": "tracking-secret-at-least-24-chars"}},
    )
    user = {"id": 3, "role": "sales", "username": "ada", "display_name": "Ada", "reply_to_email": None}
    repo = Repo()

    sent = PersonalizedEmailService(config, repo).send(9, subject=subject, body=body, user=user)
    assert sent["message_id"] == "message-9"
    assert sent["reply_to_email"] == "sales@mail.example.com"
    assert repo.contact["status"] == "sent_1"
    assert repo.draft["status"] == "sent"

    event = WebhookService(repo, config=config).process_payload(
        "resend",
        {"type": "email.received", "contact_id": 9, "data": {"subject": "Re: channel", "text": "Please send the proposal."}},
    )
    assert event == "replied"
    assert repo.contact["status"] == "replied"
    assert repo.contact["lifecycle_stage"] == "replied"
    assert repo.contact["sabcd_stage"] == "C"
    assert repo.activities[0]["content"] == "Please send the proposal."


def test_admin_send_uses_logged_in_admin_identity(monkeypatch):
    subject = "A relevant VERTU partnership idea"
    body = (
        "I noticed your premium retail positioning and thought a selective VERTU partnership "
        "could be relevant. Would a brief discussion be useful this quarter?"
    )
    captured = {}

    class Repo:
        contact = {
            "id": 11,
            "owner_user_id": 4,
            "first_name": "Lead",
            "email": "lead@example.com",
            "email_status": "valid",
            "email_confidence": 95,
            "company_name": "Example",
            "lead_score": 90,
            "sequence_step": 0,
        }

        def get_private_contact_for_user(self, contact_id, user):
            return dict(self.contact)

        def get_latest_email_draft(self, contact_id, user_id=None):
            return {"id": 7, "status": "approved", "subject": subject, "body": body}

        def reserve_send_attempt(self, *args, **kwargs):
            captured["attempt"] = kwargs
            return {"reserved": True}

        def finish_send_attempt(self, *args, **kwargs):
            pass

        def record_manual_sent(self, contact_id, step, email_subject, message_id, metadata):
            captured["metadata"] = metadata
            return True

        def mark_latest_email_draft_sent(self, contact_id, user_id=None):
            pass

    class SenderPool:
        def __init__(self, config, repo):
            pass

        def pick_sender(self):
            return {
                "id": 1,
                "provider": "smtp",
                "name": "Global",
                "email": "global@vertu.com",
                "dry_run": False,
            }

        def record_send(self, sender):
            pass

    class Mailer:
        def __init__(self, provider, api_key, sender, *, smtp_config):
            captured["provider"] = provider
            captured["sender"] = sender
            captured["smtp"] = smtp_config

        def send(self, *args, **kwargs):
            captured["send_kwargs"] = kwargs
            return "<ivan-message@example.com>"

    monkeypatch.setattr(outreach, "SenderPoolManager", SenderPool)
    monkeypatch.setattr(outreach, "MailClient", Mailer)
    config = SimpleNamespace(
        sender={"name": "Global", "email": "global@vertu.com", "provider": "smtp"},
        apis={},
        raw={
            "app": {
                "public_base_url": "https://sales.example.com",
                "tracking_signing_secret": "tracking-secret-at-least-24-chars",
            },
            "smtp": {
                "host": "smtp.exmail.qq.com",
                "username": "global@vertu.com",
                "password": "global-password",
            },
            "sales_mailboxes": {
                "ivan": {
                    "active": True,
                    "email": "ivan.yu@vertu.com",
                    "smtp": {
                        "host": "smtp.exmail.qq.com",
                        "username": "ivan.yu@vertu.com",
                        "password": "ivan-password",
                        "security": "ssl",
                    },
                }
            },
        },
    )
    admin = {"id": 1, "role": "admin", "username": "admin", "display_name": "Admin"}

    result = PersonalizedEmailService(config, Repo()).send(
        11,
        subject=subject,
        body=body,
        user=admin,
    )

    assert result["sender_email"] == "global@vertu.com"
    assert result["reply_to_email"] == "global@vertu.com"
    assert captured["provider"] == "smtp"
    assert captured["sender"]["name"] == "Admin"
    assert captured["sender"]["email"] == "global@vertu.com"
    assert captured["smtp"]["username"] == "global@vertu.com"
    assert captured["send_kwargs"]["metadata"]["user_id"] == 1
    assert captured["metadata"]["user_id"] == 1
    assert captured["metadata"]["actor_user_id"] == 1
