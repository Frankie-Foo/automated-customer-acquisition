from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

from sales_automation.web import make_handler
from sales_automation.outbound_identity import signed_reply_address


class Db:
    def is_available(self):
        return True


class Repo:
    def __init__(self):
        self.db = Db()
        self.events = []
        self.activities = []

    def find_contact_id_by_message_id(self, message_id):
        return 42 if message_id == "outbound-123" else None

    def find_contact_id_by_email(self, email):
        return 42 if email == "lead@example.com" else None

    def record_event(self, contact_id, event_type, payload):
        self.events.append((contact_id, event_type, payload))

    def add_lifecycle_activity(self, contact_id, **kwargs):
        self.activities.append((contact_id, kwargs))
        return {"id": 1}


def _post(base_url, payload, secret):
    request = urllib.request.Request(
        base_url + "/webhooks/inbound-email",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Inbound-Secret": secret},
        method="POST",
    )
    return json.loads(urllib.request.urlopen(request, timeout=5).read().decode("utf-8"))


def test_inbound_reply_records_event_and_lifecycle_activity():
    secret = "inbound-test-secret-at-least-24-chars"
    repo = Repo()
    config = SimpleNamespace(raw={"app": {}, "webhooks": {"inbound_email_secret": secret}})
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(config, repo))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response = _post(
            f"http://127.0.0.1:{server.server_port}",
            {
                "from": "lead@example.com",
                "to": "sales@vertu.com",
                "subject": "Re: channel fit",
                "text": "Please send the commercial proposal.",
                "in_reply_to": "outbound-123",
            },
            secret,
        )
        assert response["data"] == {"event_type": "replied", "contact_id": 42}
        assert repo.events[0][0:2] == (42, "replied")
        assert repo.activities[0][1]["lifecycle_stage"] == "replied"
        assert repo.activities[0][1]["content"] == "Please send the commercial proposal."
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_inbound_reply_rejects_wrong_secret():
    repo = Repo()
    secret = "inbound-test-secret-at-least-24-chars"
    config = SimpleNamespace(raw={"app": {}, "webhooks": {"inbound_email_secret": secret}})
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(config, repo))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        try:
            _post(f"http://127.0.0.1:{server.server_port}", {"from": "lead@example.com"}, "wrong-secret")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
            assert "invalid_inbound_email_secret" in exc.read().decode("utf-8")
        else:
            raise AssertionError("wrong inbound secret should fail")
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_inbound_reply_uses_signed_recipient_route_without_sender_lookup():
    secret = "inbound-test-secret-at-least-24-chars"
    routing_secret = "routing-test-secret-at-least-24-chars"

    class RoutedRepo(Repo):
        def __init__(self):
            super().__init__()
            self.routed = []

        def route_inbound_reply(self, contact_id, user_id):
            self.routed.append((contact_id, user_id))
            return {"owner_user_id": user_id, "pool_type": "private", "reply_assignment_pending": False}

    repo = RoutedRepo()
    config = SimpleNamespace(
        raw={
            "app": {},
            "webhooks": {"inbound_email_secret": secret},
            "outbound_identity": {
                "mode": "centralized_alias",
                "sending_domain": "outreach.vertu.test",
                "reply_domain": "reply.outreach.vertu.test",
                "routing_secret": routing_secret,
            },
        }
    )
    reply_to = signed_reply_address(config, contact_id=77, user_id=11, sequence_step=1)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(config, repo))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response = _post(
            f"http://127.0.0.1:{server.server_port}",
            {
                "from": "unknown-lead@example.net",
                "to": [reply_to],
                "subject": "Re: Vertu partnership",
                "text": "Please send more information.",
            },
            secret,
        )
        assert response["data"] == {"event_type": "replied", "contact_id": 77}
        assert repo.routed == [(77, 11)]
        assert repo.events[0][2]["reply_route"]["sequence_step"] == 1
    finally:
        server.shutdown()
        thread.join(timeout=5)
