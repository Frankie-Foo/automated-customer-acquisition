import pytest

from sales_automation.mcp_server import _resolve_mcp_user


class Repo:
    def list_users(self):
        return [
            {"id": 1, "username": "admin", "role": "admin", "active": True},
            {"id": 2, "username": "Chris", "role": "sales", "active": True},
            {"id": 3, "username": "Former", "role": "sales", "active": False},
        ]


def test_mcp_requires_a_process_bound_identity():
    with pytest.raises(RuntimeError, match="SALESBOT_MCP_USERNAME is required"):
        _resolve_mcp_user(Repo(), "")


def test_mcp_resolves_only_the_bound_active_user():
    assert _resolve_mcp_user(Repo(), "chris")["id"] == 2
    with pytest.raises(ValueError, match="disabled"):
        _resolve_mcp_user(Repo(), "former")
