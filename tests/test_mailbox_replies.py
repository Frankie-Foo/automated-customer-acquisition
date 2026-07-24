from __future__ import annotations

from email.message import EmailMessage
from types import SimpleNamespace

from sales_automation.services.mailbox import MailboxReplyService


class FakeImap:
    def __init__(self, messages, *, username="frank.fu@vertu.com", password="client-password"):
        self.messages = messages
        self.fetch_queries = []
        self.username = username
        self.password = password

    def login(self, username, password):
        assert username == self.username
        assert password == self.password
        return "OK", []

    def select(self, folder, readonly=False):
        assert folder == "INBOX"
        assert readonly is True
        return "OK", [str(len(self.messages)).encode()]

    def uid(self, command, *args):
        if command == "search":
            return "OK", [b" ".join(str(index + 1).encode() for index in range(len(self.messages)))]
        if command == "fetch":
            uid = int(args[0])
            self.fetch_queries.append(args[1])
            return "OK", [(b"message", self.messages[uid - 1])]
        raise AssertionError(command)

    def logout(self):
        return "BYE", []


class Repo:
    def __init__(self):
        self.deliveries = set()
        self.processed = []

    def find_contact_id_by_message_id(self, message_id):
        return 42 if message_id == "<outbound-42@vertu.com>" else None

    def find_contact_id_by_email(self, email):
        return 42 if email == "lead@example.com" else None

    def record_webhook_delivery(self, provider, event_type, payload, external_id):
        key = (provider, external_id)
        if key in self.deliveries:
            return False
        self.deliveries.add(key)
        return True

    def mark_webhook_delivery_processed(self, provider, external_id):
        self.processed.append((provider, external_id))


class Webhook:
    def __init__(self):
        self.payloads = []

    def process_payload(self, provider, payload):
        self.payloads.append((provider, payload))
        return "replied"


def config():
    return SimpleNamespace(
        raw={
            "imap": {
                "host": "imap.exmail.qq.com",
                "port": 993,
                "username": "frank.fu@vertu.com",
                "password": "client-password",
                "folder": "INBOX",
                "lookback_days": 14,
            },
            "smtp": {},
            "notifications": {},
        }
    )


def reply_message(*, sender="lead@example.com", message_id="<reply-1@example.com>", in_reply_to="<outbound-42@vertu.com>"):
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "frank.fu@vertu.com"
    message["Subject"] = "Re: VERTU partnership"
    message["Message-ID"] = message_id
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to
    message.set_content("Please send the commercial proposal.")
    return message.as_bytes()


def test_mailbox_poll_matches_outbound_message_without_marking_mail_read():
    mailbox = FakeImap([reply_message()])
    repo = Repo()
    webhook = Webhook()
    service = MailboxReplyService(config(), repo, imap_factory=lambda *args, **kwargs: mailbox, webhook_service=webhook)

    stats = service.poll_once(10)

    assert stats == {"scanned": 1, "matched": 1, "recorded": 1, "duplicates": 0, "ignored": 0}
    assert mailbox.fetch_queries == ["(BODY.PEEK[])"]
    assert webhook.payloads[0][0] == "imap"
    assert webhook.payloads[0][1]["contact_id"] == 42
    assert webhook.payloads[0][1]["text"] == "Please send the commercial proposal."
    assert repo.processed == [("imap", "<reply-1@example.com>")]


def test_mailbox_poll_is_idempotent_and_ignores_unknown_sender():
    mailbox = FakeImap([
        reply_message(),
        reply_message(sender="unknown@example.net", message_id="<unknown@example.net>", in_reply_to=""),
    ])
    repo = Repo()
    webhook = Webhook()
    service = MailboxReplyService(config(), repo, imap_factory=lambda *args, **kwargs: mailbox, webhook_service=webhook)

    first = service.poll_once(10)
    second = service.poll_once(10)

    assert first["recorded"] == 1
    assert first["ignored"] == 1
    assert second["duplicates"] == 1
    assert second["ignored"] == 1
    assert len(webhook.payloads) == 1


def test_mailbox_poll_ignores_automated_messages():
    message = EmailMessage()
    message["From"] = "postmaster@example.com"
    message["To"] = "frank.fu@vertu.com"
    message["Message-ID"] = "<bounce@example.com>"
    message["In-Reply-To"] = "<outbound-42@vertu.com>"
    message["Auto-Submitted"] = "auto-generated"
    message.set_content("Delivery report")
    mailbox = FakeImap([message.as_bytes()])
    webhook = Webhook()

    stats = MailboxReplyService(config(), Repo(), imap_factory=lambda *args, **kwargs: mailbox, webhook_service=webhook).poll_once(10)

    assert stats["ignored"] == 1
    assert not webhook.payloads


def test_mailbox_poll_aggregates_global_and_sales_mailboxes():
    global_mailbox = FakeImap(
        [reply_message(message_id="<global-reply@example.com>")],
    )
    ivan_mailbox = FakeImap(
        [reply_message(message_id="<ivan-reply@example.com>")],
        username="ivan.yu@vertu.com",
        password="ivan-password",
    )
    mailboxes = iter([global_mailbox, ivan_mailbox])
    cfg = config()
    cfg.raw["sales_mailboxes"] = {
        "ivan": {
            "active": True,
            "smtp": {
                "username": "ivan.yu@vertu.com",
                "password": "ivan-password",
            },
            "imap": {
                "host": "imap.exmail.qq.com",
                "username": "ivan.yu@vertu.com",
                "password": "ivan-password",
                "folder": "INBOX",
            },
        }
    }
    repo = Repo()
    webhook = Webhook()
    service = MailboxReplyService(
        cfg,
        repo,
        imap_factory=lambda *args, **kwargs: next(mailboxes),
        webhook_service=webhook,
    )

    stats = service.poll_once(10)

    assert stats == {"scanned": 2, "matched": 2, "recorded": 2, "duplicates": 0, "ignored": 0}
    assert webhook.payloads[0][1]["mailbox_key"] is None
    assert webhook.payloads[1][1]["mailbox_key"] == "ivan"
