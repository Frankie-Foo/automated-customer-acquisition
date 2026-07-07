from types import SimpleNamespace

from sales_automation.clients import MailClient
from sales_automation.services.outreach import _normalize_sender_signature, _reply_to_email, _sender_signature


class RecordingHttp:
    def __init__(self):
        self.calls = []

    def request(self, method, url, *, headers=None, json_body=None, **kwargs):
        self.calls.append({"method": method, "url": url, "headers": headers, "json_body": json_body})
        return {"id": "email_123"}


def test_resend_mail_client_sets_reply_to():
    http = RecordingHttp()
    sender = {"name": "vertuMay", "email": "vertuMay@mail.frelys.xyz", "dry_run": False}
    client = MailClient("resend", "key", sender, http=http)

    message_id = client.send("lead@example.com", "Subject", "<p>Hello</p>", "Hello", reply_to="sales@vertu.cn")

    assert message_id == "email_123"
    assert http.calls[0]["json_body"]["reply_to"] == ["sales@vertu.cn"]


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
