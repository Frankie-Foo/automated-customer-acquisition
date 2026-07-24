from __future__ import annotations

from typing import Any

from .config import AppConfig


def sender_identity_user(repo: Any, contact: dict[str, Any], actor: dict[str, Any] | None) -> dict[str, Any] | None:
    """Use the logged-in actor; background jobs fall back to the contact owner."""

    if actor:
        return actor
    owner_id = int(contact.get("owner_user_id") or 0)
    if not owner_id:
        return None
    if hasattr(repo, "get_user_by_id"):
        owner = repo.get_user_by_id(owner_id)
        if owner and owner.get("active", True):
            return owner
    return None


def sales_mailbox(config: AppConfig, user: dict[str, Any] | None) -> dict[str, Any] | None:
    mailbox = _configured_sales_mailbox(config, user)
    if mailbox and _enabled(mailbox.get("active", True)):
        return mailbox
    return None


def _configured_sales_mailbox(config: AppConfig, user: dict[str, Any] | None) -> dict[str, Any] | None:
    if not user:
        return None
    mailboxes = config.raw.get("sales_mailboxes", {})
    if not isinstance(mailboxes, dict):
        return None
    keys = (
        str(user.get("username") or "").strip().lower(),
        str(user.get("id") or "").strip(),
    )
    for key in keys:
        mailbox = mailboxes.get(key)
        if isinstance(mailbox, dict):
            return mailbox
    return None


def sender_transport_for_user(
    config: AppConfig,
    user: dict[str, Any] | None,
    default_sender: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the selected transport and SMTP settings for a sales-owned mailbox."""

    global_smtp = dict(config.raw.get("smtp", {}))
    configured_mailbox = _configured_sales_mailbox(config, user)
    if configured_mailbox and not _enabled(configured_mailbox.get("active", True)):
        raise RuntimeError(f"Sales mailbox is not active for {str((user or {}).get('username') or 'this user')}")
    mailbox = configured_mailbox
    if not mailbox:
        return default_sender, global_smtp
    smtp = {**global_smtp, **dict(mailbox.get("smtp") or {})}
    username = str(smtp.get("username") or mailbox.get("email") or "").strip()
    if not (smtp.get("host") and username and smtp.get("password")):
        raise RuntimeError(f"Sales mailbox credentials are incomplete for {str((user or {}).get('username') or 'this user')}")
    sender = {
        **default_sender,
        "provider": "smtp",
        "email": str(mailbox.get("email") or username).strip(),
        "name": str((user or {}).get("display_name") or default_sender.get("name") or "VERTU").strip(),
    }
    smtp["username"] = username
    smtp["envelope_from"] = str(smtp.get("envelope_from") or username).strip()
    return sender, smtp


def configured_imap_mailboxes(config: AppConfig) -> list[dict[str, Any]]:
    """Return the global mailbox plus active per-sales mailboxes, deduplicated by login."""

    settings: list[dict[str, Any]] = [_imap_settings(config.raw.get("imap", {}), config.raw.get("smtp", {}), None)]
    mailboxes = config.raw.get("sales_mailboxes", {})
    if isinstance(mailboxes, dict):
        for key, mailbox in mailboxes.items():
            if not isinstance(mailbox, dict) or not _enabled(mailbox.get("active", True)):
                continue
            settings.append(
                _imap_settings(
                    mailbox.get("imap", {}),
                    mailbox.get("smtp", {}),
                    str(key),
                )
            )
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in settings:
        identity = (str(item.get("host") or "").lower(), str(item.get("username") or "").lower())
        if not all(identity) or identity in seen:
            continue
        seen.add(identity)
        deduped.append(item)
    return deduped


def _imap_settings(imap: Any, smtp: Any, mailbox_key: str | None) -> dict[str, Any]:
    imap = imap if isinstance(imap, dict) else {}
    smtp = smtp if isinstance(smtp, dict) else {}
    return {
        "host": str(imap.get("host") or "").strip(),
        "port": int(imap.get("port") or 993),
        "username": str(imap.get("username") or smtp.get("username") or "").strip(),
        "password": str(imap.get("password") or smtp.get("password") or ""),
        "folder": str(imap.get("folder") or "INBOX"),
        "timeout": int(imap.get("timeout") or 20),
        "lookback_days": max(1, int(imap.get("lookback_days") or 14)),
        "mailbox_key": mailbox_key,
    }


def _enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() not in {"", "0", "false", "no", "off"}


__all__ = [
    "configured_imap_mailboxes",
    "sales_mailbox",
    "sender_identity_user",
    "sender_transport_for_user",
]
