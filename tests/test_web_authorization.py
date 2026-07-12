from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

from sales_automation import web


class FakeRepo:
    db = object()

    def __init__(self):
        self.deleted_tokens = []

    def get_session_user(self, token):
        if token == "sales-token":
            return {
                "id": 2,
                "username": "sales",
                "display_name": "Sales",
                "role": "sales",
                "daily_source_limit": 100,
                "daily_send_limit": 100,
            }
        return None

    def delete_session(self, token):
        self.deleted_tokens.append(token)


def test_sales_user_cannot_run_global_admin_operations(monkeypatch):
    monkeypatch.setattr(web, "check_database", lambda repo: {"ok": True})
    handler = web.make_handler(SimpleNamespace(raw={"app": {}}), FakeRepo())
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    try:
        for path in ("/api/migrate", "/api/scheduler", "/api/send", "/api/send-one"):
            req = urllib.request.Request(
                base_url + path,
                data=json.dumps({}).encode("utf-8"),
                headers={"Content-Type": "application/json", "Cookie": "salesbot_session=sales-token"},
                method="POST",
            )
            try:
                urllib.request.urlopen(req, timeout=5)
            except urllib.error.HTTPError as exc:
                assert exc.code == 403
                assert json.loads(exc.read().decode("utf-8"))["error"] == "admin_required"
            else:
                raise AssertionError(f"{path} should require admin")
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_post_logout_deletes_session_and_expires_cookie(monkeypatch):
    monkeypatch.setattr(web, "check_database", lambda repo: {"ok": True})
    repo = FakeRepo()
    handler = web.make_handler(SimpleNamespace(raw={"app": {}}), repo)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    try:
        req = urllib.request.Request(
            base_url + "/api/logout",
            data=json.dumps({}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Cookie": "salesbot_session=sales-token"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            assert response.status == 200
            assert repo.deleted_tokens == ["sales-token"]
            cookie = response.headers.get("Set-Cookie") or ""
            assert "salesbot_session=" in cookie
            assert "Max-Age=0" in cookie
    finally:
        server.shutdown()
        thread.join(timeout=5)
