from types import SimpleNamespace

import pytest

from sales_automation.mailbox_accounts import (
    configured_imap_mailboxes,
    sender_transport_for_user,
    sender_identity_user,
)


class Repo:
    def get_user_by_id(self, user_id):
        if user_id == 4:
            return {
                "id": 4,
                "username": "Ivan",
                "display_name": "Ivan",
                "reply_to_email": "ivan.yu@vertu.com",
                "active": True,
            }
        return None


def config():
    return SimpleNamespace(
        raw={
            "smtp": {
                "host": "smtp.global.test",
                "username": "global@example.test",
                "password": "global-password",
            },
            "imap": {
                "host": "imap.global.test",
                "username": "global@example.test",
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
                    "imap": {
                        "host": "imap.exmail.qq.com",
                        "username": "ivan.yu@vertu.com",
                        "password": "ivan-password",
                    },
                },
                "april": {
                    "active": False,
                    "email": "april.yang@vertu.cn",
                },
            },
        }
    )


def test_logged_in_actor_identity_wins_over_contact_owner():
    admin = {"id": 1, "username": "admin", "display_name": "Admin"}

    sender_user = sender_identity_user(Repo(), {"owner_user_id": 4}, admin)

    assert sender_user["username"] == "admin"


def test_background_send_falls_back_to_contact_owner():
    sender_user = sender_identity_user(Repo(), {"owner_user_id": 4}, None)

    assert sender_user["username"] == "Ivan"
    assert sender_user["reply_to_email"] == "ivan.yu@vertu.com"


def test_logged_in_sales_mailbox_overrides_default_transport():
    default_sender = {
        "id": 9,
        "provider": "smtp",
        "name": "Global",
        "email": "global@example.test",
        "dry_run": False,
    }

    sender, smtp = sender_transport_for_user(
        config(),
        {"id": 4, "username": "Ivan", "display_name": "Ivan"},
        default_sender,
    )

    assert sender["email"] == "ivan.yu@vertu.com"
    assert sender["name"] == "Ivan"
    assert sender["provider"] == "smtp"
    assert smtp["username"] == "ivan.yu@vertu.com"
    assert smtp["password"] == "ivan-password"
    assert smtp["envelope_from"] == "ivan.yu@vertu.com"


def test_unconfigured_salesperson_keeps_default_transport():
    default_sender = {"provider": "smtp", "email": "global@example.test"}

    sender, smtp = sender_transport_for_user(
        config(),
        {"id": 8, "username": "May"},
        default_sender,
    )

    assert sender == default_sender
    assert smtp["username"] == "global@example.test"


def test_declared_but_inactive_sales_mailbox_fails_closed():
    with pytest.raises(RuntimeError, match="not active for April"):
        sender_transport_for_user(
            config(),
            {"id": 3, "username": "April"},
            {"provider": "smtp", "email": "global@example.test"},
        )


def test_configured_imap_mailboxes_include_active_sales_accounts_only():
    mailboxes = configured_imap_mailboxes(config())

    assert [mailbox["username"] for mailbox in mailboxes] == [
        "global@example.test",
        "ivan.yu@vertu.com",
    ]
    assert mailboxes[1]["mailbox_key"] == "ivan"
