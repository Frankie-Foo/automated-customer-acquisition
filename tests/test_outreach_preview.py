"""Loopback HTTP checks with fake data; no configured database or mail transport."""
import json
import threading
from http.client import HTTPResponse
from http.server import ThreadingHTTPServer
from socket import create_connection
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from sales_automation import web
from sales_automation.db import Repository
from tools.preview_outreach_client import make_preview_handler


SALES = {
    "id": 2, "username": "sales", "display_name": "Sales", "role": "sales",
    "daily_source_limit": 100, "daily_send_limit": 100,
}


@pytest.fixture
def preview_api(monkeypatch):
    repo = Mock(spec=Repository)
    repo.db = Mock()
    repo.db.connect.side_effect = AssertionError("Preview tests must not connect to a database")
    sessions = {
        "sales-token": dict(SALES),
        "admin-token": {**SALES, "id": 1, "role": "admin"},
    }
    repo.get_session_user.side_effect = sessions.get
    repo.delete_session.side_effect = lambda token: sessions.pop(token, None)
    repo.authenticate_user.side_effect = lambda username, password: (
        SALES if (username, password) == ("sales", "test-password") else None
    )
    repo.create_session.return_value = "new-token"
    repo.change_own_password.return_value = {**SALES, "must_change_password": False}
    repo.usage_for_user.return_value = {"source_count": 0, "send_count": 0}
    repo.global_usage.return_value = {"source_count": 0, "send_count": 0}
    repo.list_followup_tasks.return_value = []
    monkeypatch.setattr(web, "check_database", lambda repo: {"ok": True})
    sync_accounts = Mock(side_effect=AssertionError("Sender sync must not run in preview"))
    monkeypatch.setattr(web.SenderPoolManager, "sync_accounts", sync_accounts)
    handler = make_preview_handler(SimpleNamespace(raw={"app": {}}), repo)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()

    def request(path, *, method="GET", token="sales-token", payload=None, headers=None):
        host = f"127.0.0.1:{server.server_port}"
        request_headers = {"Host": host, "Origin": f"http://{host}"}
        if token:
            request_headers["Cookie"] = f"salesbot_session={token}"
        body = b""
        if method == "POST":
            body = json.dumps(payload or {}).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
            request_headers["Content-Length"] = str(len(body))
        request_headers.update(headers or {})
        lines = [f"{method} {path} HTTP/1.0", *(f"{key}: {value}" for key, value in request_headers.items())]
        with create_connection(("127.0.0.1", server.server_port), timeout=5) as connection:
            # One write avoids a Windows reset when headers are rejected before the body arrives.
            connection.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + body)
            with HTTPResponse(connection) as response:
                response.begin()
                data = response.read()
                if "application/json" in response.getheader("Content-Type", ""):
                    data = json.loads(data)
                return response.status, response.headers, data

    try:
        yield SimpleNamespace(request=request, repo=repo, sessions=sessions, sync_accounts=sync_accounts)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("path", [
    "/api/admin/senders", "/api/admin/senders?refresh=1",
    "/unsubscribe?contact_id=10", "/track/open?contact_id=10&step=1",
])
def test_mutating_gets_are_blocked_even_for_admin(preview_api, path):
    status, _, _ = preview_api.request(path, token="admin-token")
    assert status == 404
    preview_api.sync_accounts.assert_not_called()
    assert preview_api.repo.mock_calls == []


def test_post_logout_revokes_session_and_expires_cookie(preview_api):
    status, headers, data = preview_api.request("/api/logout", method="POST")
    assert status == 200 and data["ok"] is True
    preview_api.repo.delete_session.assert_called_once_with("sales-token")
    assert "sales-token" not in preview_api.sessions
    assert "salesbot_session=" in headers["Set-Cookie"]
    assert "Max-Age=0" in headers["Set-Cookie"]
    assert preview_api.request("/api/followup-tasks")[0] == 401


@pytest.mark.parametrize("headers, status, error", [
    ({"Origin": "https://attacker.example"}, 403, "csrf_origin_rejected"),
    ({"Sec-Fetch-Site": "cross-site"}, 403, "csrf_origin_rejected"),
    ({"Content-Type": "text/plain"}, 415, "json_content_type_required"),
])
def test_logout_keeps_existing_safe_post_guards(preview_api, headers, status, error):
    actual, _, data = preview_api.request("/api/logout", method="POST", headers=headers)
    assert actual == status and data["error"] == error
    preview_api.repo.delete_session.assert_not_called()
    assert "sales-token" in preview_api.sessions


@pytest.mark.parametrize("path", [
    "/api/send-custom", "/api/email-draft", "/api/contacts",
    "/api/admin/sender", "/webhooks/resend",
])
def test_business_writes_and_sends_remain_disabled(preview_api, path):
    status, _, data = preview_api.request(path, method="POST", token="admin-token", payload={"contact_id": 10})
    assert status == 403
    assert data == {"ok": False, "error": "Read-only preview; changes and sends are disabled"}
    assert preview_api.repo.mock_calls == []


@pytest.mark.parametrize("password, expected_status", [("test-password", 200), ("wrong-password", 401)])
def test_login_still_authenticates_credentials(preview_api, password, expected_status):
    status, headers, data = preview_api.request(
        "/api/login", method="POST", token=None,
        payload={"username": "sales", "password": password},
    )
    assert status == expected_status
    preview_api.repo.authenticate_user.assert_called_once_with("sales", password)
    if status == 200:
        assert data["data"]["user"]["id"] == SALES["id"]
        assert "salesbot_session=new-token" in headers["Set-Cookie"]
        preview_api.repo.create_session.assert_called_once_with(SALES["id"])
    else:
        preview_api.repo.create_session.assert_not_called()
        assert headers.get("Set-Cookie") is None


@pytest.mark.parametrize("token", [None, "expired-token"])
def test_password_change_requires_authenticated_session(preview_api, token):
    status, _, data = preview_api.request(
        "/api/change-password", method="POST", token=token,
        payload={"current_password": "test-password", "new_password": "new-test-password"},
    )
    assert status == 401 and data["error"] == "unauthorized"
    preview_api.repo.change_own_password.assert_not_called()


@pytest.mark.parametrize("must_change_password", [False, True])
def test_password_change_uses_session_identity_not_payload(preview_api, must_change_password):
    preview_api.sessions["sales-token"]["must_change_password"] = must_change_password
    status, _, data = preview_api.request(
        "/api/change-password", method="POST",
        payload={"user_id": 1, "current_password": "test-password", "new_password": "new-test-password"},
    )
    assert status == 200
    assert data["data"]["user"]["id"] == SALES["id"]
    assert data["data"]["user"]["must_change_password"] is False
    preview_api.repo.change_own_password.assert_called_once_with(2, "test-password", "new-test-password")


def test_authenticated_read_still_works(preview_api):
    status, _, data = preview_api.request("/api/followup-tasks")
    assert status == 200 and data["data"] == {"tasks": []}
    preview_api.repo.list_followup_tasks.assert_called_once_with(user=SALES, status="open", limit=100)
