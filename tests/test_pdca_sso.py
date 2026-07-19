from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from sales_automation.pdca_sso import PdcaSsoError, PdcaSsoService


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_pdca_sso_exchanges_scoped_profile(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, json.loads(request.data)))
        return FakeResponse({
            "ok": True,
            "profile": {
                "subject": "pdca:17",
                "username": "april",
                "display_name": "April",
                "role": "sales",
                "data_scope": "self",
                "owner_key": "april",
                "team_key": "overseas-a",
                "owner_keys": ["april", "April"],
            },
        })

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    config = SimpleNamespace(raw={"sso": {
        "pdca_enabled": "true",
        "pdca_exchange_url": "https://pdca-workbench-teams.vertu.cn/api/auth/acquisition/exchange",
    }})

    profile = PdcaSsoService(config).exchange("x" * 43)

    assert profile.subject == "pdca:17"
    assert profile.owner_keys == ("april", "April")
    assert calls == [(config.raw["sso"]["pdca_exchange_url"], {"code": "x" * 43})]


def test_pdca_sso_rejects_non_sales_roles(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: FakeResponse({
        "ok": True,
        "profile": {
            "subject": "pdca:3",
            "username": "dealer",
            "role": "dealer",
            "data_scope": "self",
        },
    }))
    config = SimpleNamespace(raw={"sso": {
        "pdca_enabled": True,
        "pdca_exchange_url": "https://pdca-workbench-teams.vertu.cn/api/auth/acquisition/exchange",
    }})

    with pytest.raises(PdcaSsoError) as denied:
        PdcaSsoService(config).exchange("y" * 43)
    assert denied.value.status == 403


def test_pdca_sso_rejects_insecure_remote_exchange_url():
    config = SimpleNamespace(raw={"sso": {
        "pdca_enabled": True,
        "pdca_exchange_url": "http://pdca.example.test/exchange",
    }})

    with pytest.raises(PdcaSsoError) as invalid:
        PdcaSsoService(config).exchange("z" * 43)
    assert invalid.value.status == 500
