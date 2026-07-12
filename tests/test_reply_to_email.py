from types import SimpleNamespace

from sales_automation.clients import MailClient
from sales_automation.services.outreach import _normalize_sender_signature, _reply_to_email, _sender_signature


class RecordingHttp:
    def __init__(self):
        self.calls = []

    def request(self, method, url, *, headers=None, json_body=None, **kwargs):
        self.calls.append({"method": method, "url": url, "headers": headers, "json_body": json_body})
        return {"id": "email_123"}


class RecordingSmtp:
    def __init__(self):
        self.login_args = None
        self.sent = None
        self.starttls_called = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def ehlo(self):
        return None

    def starttls(self, context=None):
        self.starttls_called = True

    def login(self, username, password):
        self.login_args = (username, password)

    def send_message(self, message, *, from_addr, to_addrs):
        self.sent = {"message": message, "from_addr": from_addr, "to_addrs": to_addrs}


def test_resend_mail_client_sets_reply_to():
    http = RecordingHttp()
    sender = {"name": "vertuMay", "email": "vertuMay@mail.frelys.xyz", "dry_run": False}
    client = MailClient("resend", "key", sender, http=http)

    message_id = client.send("lead@example.com", "Subject", "<p>Hello</p>", "Hello", reply_to="sales@vertu.cn")

    assert message_id == "email_123"
    assert http.calls[0]["json_body"]["reply_to"] == ["sales@vertu.cn"]


def test_resend_mail_client_sets_idempotency_key():
    http = RecordingHttp()
    sender = {"name": "vertuMay", "email": "vertuMay@mail.frelys.xyz", "dry_run": False}
    client = MailClient("resend", "key", sender, http=http)

    client.send("lead@example.com", "Subject", "<p>Hello</p>", "Hello", idempotency_key="contact-42-step-1")

    assert http.calls[0]["headers"]["Idempotency-Key"] == "contact-42-step-1"


def test_smtp_mail_client_sends_multipart_with_signed_reply_to():
    smtp = RecordingSmtp()
    sender = {"name": "Viki", "email": "partnerships@outreach.vertu.test", "dry_run": False}
    client = MailClient(
        "smtp",
        "",
        sender,
        smtp_config={
            "host": "smtp.example.test",
            "port": 465,
            "username": "smtp-user@example.test",
            "password": "client-password",
            "security": "ssl",
            "envelope_from": "smtp-user@example.test",
        },
        smtp_factory=lambda: smtp,
    )

    message_id = client.send(
        "lead@example.com",
        "Subject",
        "<p>Hello</p>",
        "Hello",
        metadata={"contact_id": 42, "unknown": "ignored"},
        reply_to="reply+signed@reply.example.test",
    )

    message = smtp.sent["message"]
    assert message_id == message["Message-ID"]
    assert message["From"] == "Viki <partnerships@outreach.vertu.test>"
    assert message["Reply-To"] == "reply+signed@reply.example.test"
    assert message["X-Salesbot-Contact-ID"] == "42"
    assert message["X-Salesbot-Unknown"] is None
    assert message.is_multipart()
    assert smtp.login_args == ("smtp-user@example.test", "client-password")
    assert smtp.sent["from_addr"] == "smtp-user@example.test"
    assert smtp.sent["to_addrs"] == ["lead@example.com"]


def test_smtp_mail_client_supports_starttls():
    smtp = RecordingSmtp()
    client = MailClient(
        "smtp",
        "",
        {"name": "Sales", "email": "sales@example.test", "dry_run": False},
        smtp_config={"host": "smtp.example.test", "port": 587, "username": "sales@example.test", "password": "pw", "security": "starttls"},
        smtp_factory=lambda: smtp,
    )

    client.send("lead@example.com", "Subject", "<p>Hello</p>", "Hello")

    assert smtp.starttls_called is True


def test_reply_to_email_rejects_missing_or_malformed_user_value():
    assert _reply_to_email({"reply_to_email": "sales@vertu.cn"}) == "sales@vertu.cn"
    assert _reply_to_email({"reply_to_email": "bad value"}) is None
    assert _reply_to_email(None) is None


def test_sender_signature_uses_logged_in_user_identity():
    assert _sender_signature({"display_name": "Viki"}, "vertuMay") == (
        "Best regards,\n"
        "Viki You\n"
        "BD Manager Of Media East Region | VERTU"
    )


def test_normalize_sender_signature_replaces_generic_signoff_and_keeps_unsubscribe():
    text = "Hi Ada,\n\nRelevant note.\n\nBest,\nvertuMay\n\nUnsubscribe: https://example.test/u"

    normalized = _normalize_sender_signature(text, {"display_name": "Safae"}, fallback_name="vertuMay")

    assert "Best,\nvertuMay" not in normalized
    assert "Best regards,\nSafae You\nBD Manager Of Media East Region | VERTU" in normalized
    assert normalized.endswith("Unsubscribe: https://example.test/u")
