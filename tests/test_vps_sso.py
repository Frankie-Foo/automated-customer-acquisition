from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from sales_automation.auth import session_cookie
from sales_automation.vps_sso import VpsSsoError, VpsSsoService


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_vps_sso_verifies_odoo_session_and_employee(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, request.headers.get("Cookie")))
        if request.full_url.endswith("/web/session/get_session_info"):
            return FakeResponse({"result": {"uid": 42, "username": "ivan.yu@vertu.com", "name": "Ivan"}})
        return FakeResponse({"result": [{"name": "于冰", "barcode": "800042", "work_email": "ivan.yu@vertu.com", "department_id": [7, "BD"]}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    config = SimpleNamespace(raw={"sso": {"vps_enabled": "true", "odoo_base_url": "https://admin.vertu.cn"}})

    profile = VpsSsoService(config).verify(session_id="odoo-session", user_id=42)

    assert profile.odoo_user_id == 42
    assert profile.name == "于冰"
    assert profile.email == "ivan.yu@vertu.com"
    assert profile.barcode == "800042"
    assert profile.department == "BD"
    assert calls[0][1] == "session_id=odoo-session"


def test_vps_sso_rejects_uid_mismatch(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: FakeResponse({"result": {"uid": 7}}))
    config = SimpleNamespace(raw={"sso": {"vps_enabled": "true", "odoo_base_url": "https://admin.vertu.cn"}})

    with pytest.raises(VpsSsoError) as exc:
        VpsSsoService(config).verify(session_id="odoo-session", user_id=42)

    assert exc.value.status == 401


def test_iframe_session_cookie_can_use_samesite_none():
    cookie = session_cookie("token", secure=True, same_site="None")

    assert "SameSite=None" in cookie
    assert "Secure" in cookie
