from __future__ import annotations

import imaplib
import re
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import getaddresses
from html import unescape
from typing import Any, Callable

from ..clients import SlackClient
from ..config import AppConfig
from ..db import Repository
from ..logging_utils import log
from .webhooks import WebhookService


_MESSAGE_ID_RE = re.compile(r"<[^<>]+>")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


class MailboxReplyService:
    """Read reply messages through IMAP without changing mailbox read state."""

    def __init__(
        self,
        config: AppConfig,
        repo: Repository,
        *,
        imap_factory: Callable[..., Any] | None = None,
        webhook_service: WebhookService | None = None,
    ):
        self.config = config
        self.repo = repo
        self.imap_factory = imap_factory or imaplib.IMAP4_SSL
        notifier = SlackClient(config.raw.get("notifications", {}).get("slack_webhook_url"))
        self.webhook_service = webhook_service or WebhookService(repo, notifier, config=config)

    def poll_once(self, limit: int = 100) -> dict[str, int]:
        settings = _mailbox_settings(self.config)
        if not settings["host"] or not settings["username"] or not settings["password"]:
            raise RuntimeError("IMAP_HOST, IMAP_USER and IMAP_PASSWORD are required")

        stats = {"scanned": 0, "matched": 0, "recorded": 0, "duplicates": 0, "ignored": 0}
        client = self.imap_factory(settings["host"], settings["port"], timeout=settings["timeout"])
        try:
            client.login(settings["username"], settings["password"])
            status, _ = client.select(settings["folder"], readonly=True)
            if status != "OK":
                raise RuntimeError(f"Unable to select IMAP folder: {settings['folder']}")
            since = (datetime.now(timezone.utc) - timedelta(days=settings["lookback_days"])).strftime("%d-%b-%Y")
            status, rows = client.uid("search", None, "SINCE", since)
            if status != "OK":
                raise RuntimeError("IMAP search failed")
            uids = (rows[0] or b"").split()
            for uid in reversed(uids[-max(1, min(int(limit), 500)) :]):
                status, fetched = client.uid("fetch", uid, "(BODY.PEEK[])")
                if status != "OK":
                    stats["ignored"] += 1
                    continue
                raw = _raw_message(fetched)
                if not raw:
                    stats["ignored"] += 1
                    continue
                stats["scanned"] += 1
                message = BytesParser(policy=policy.default).parsebytes(raw)
                sender = _first_address(message.get("From"))
                if not sender or sender.lower() == settings["username"].lower() or _is_automated(message, sender):
                    stats["ignored"] += 1
                    continue
                contact_id, outbound_message_id = self._match_contact(message, sender)
                if not contact_id:
                    stats["ignored"] += 1
                    continue
                stats["matched"] += 1
                incoming_message_id = _first_message_id(message.get("Message-ID"))
                external_id = incoming_message_id or f"{settings['username'].lower()}:{uid.decode('ascii', 'ignore')}"
                payload = {
                    "event_type": "replied",
                    "contact_id": contact_id,
                    "from": sender,
                    "to": [address for _, address in getaddresses(message.get_all("To", [])) if address],
                    "subject": str(message.get("Subject") or "Email reply")[:300],
                    "text": _message_text(message)[:10000],
                    "message_id": incoming_message_id,
                    "in_reply_to": outbound_message_id,
                    "source": "imap_mailbox",
                }
                if not self.repo.record_webhook_delivery("imap", "replied", payload, external_id):
                    stats["duplicates"] += 1
                    continue
                try:
                    self.webhook_service.process_payload("imap", payload)
                    self.repo.mark_webhook_delivery_processed("imap", external_id)
                    stats["recorded"] += 1
                except Exception:
                    log("mailbox.reply_failed", external_id=external_id, contact_id=contact_id)
                    raise
        finally:
            try:
                client.logout()
            except Exception:
                pass
        log("mailbox.poll_completed", **stats)
        return stats

    def _match_contact(self, message: Message, sender: str) -> tuple[int | None, str | None]:
        references = []
        for header in ("In-Reply-To", "References"):
            references.extend(_message_ids(message.get(header)))
        for message_id in reversed(references):
            contact_id = self.repo.find_contact_id_by_message_id(message_id)
            if contact_id:
                return contact_id, message_id
        return self.repo.find_contact_id_by_email(sender), None


def _mailbox_settings(config: AppConfig) -> dict[str, Any]:
    imap = config.raw.get("imap", {})
    smtp = config.raw.get("smtp", {})
    return {
        "host": str(imap.get("host") or "").strip(),
        "port": int(imap.get("port") or 993),
        "username": str(imap.get("username") or smtp.get("username") or "").strip(),
        "password": str(imap.get("password") or smtp.get("password") or ""),
        "folder": str(imap.get("folder") or "INBOX"),
        "timeout": int(imap.get("timeout") or 20),
        "lookback_days": max(1, int(imap.get("lookback_days") or 14)),
    }


def _raw_message(fetched: Any) -> bytes | None:
    for item in fetched or []:
        if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes):
            return item[1]
    return None


def _message_ids(value: Any) -> list[str]:
    return _MESSAGE_ID_RE.findall(str(value or ""))


def _first_message_id(value: Any) -> str | None:
    values = _message_ids(value)
    return values[0] if values else None


def _first_address(value: Any) -> str | None:
    addresses = getaddresses([str(value or "")])
    return addresses[0][1].strip() if addresses and addresses[0][1] else None


def _is_automated(message: Message, sender: str) -> bool:
    auto_submitted = str(message.get("Auto-Submitted") or "").lower()
    precedence = str(message.get("Precedence") or "").lower()
    localpart = sender.split("@", 1)[0].lower()
    return (
        auto_submitted not in {"", "no"}
        or precedence in {"bulk", "junk", "list"}
        or localpart in {"mailer-daemon", "postmaster"}
    )


def _message_text(message: Message) -> str:
    if message.is_multipart():
        html = ""
        for part in message.walk():
            if part.get_content_disposition() == "attachment":
                continue
            content_type = part.get_content_type()
            if content_type == "text/plain":
                return str(part.get_content() or "").strip()
            if content_type == "text/html" and not html:
                html = str(part.get_content() or "")
        return _plain_html(html)
    content = str(message.get_content() or "")
    return _plain_html(content) if message.get_content_type() == "text/html" else content.strip()


def _plain_html(value: str) -> str:
    return unescape(_HTML_TAG_RE.sub(" ", value or "")).replace("\xa0", " ").strip()


__all__ = ["MailboxReplyService"]
