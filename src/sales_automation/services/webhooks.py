from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..clients import SlackClient
from ..db import Repository
from ..logging_utils import log
from ..outreach_guard import annotate_delivery_payload


class WebhookService:
    def __init__(self, repo: Repository, notifier: SlackClient | None = None):
        self.repo = repo
        self.notifier = notifier

    def process_payload(self, provider: str, payload: dict[str, Any]) -> str:
        contact_id = _extract_contact_id(payload)
        event_type = _extract_event_type(provider, payload)
        if not contact_id:
            message_id = _extract_message_id(payload)
            if message_id:
                contact_id = self.repo.find_contact_id_by_message_id(message_id)
        if not contact_id:
            raise ValueError("Webhook payload does not include contact_id metadata or a known message id")
        payload = annotate_delivery_payload(event_type, payload)
        self.repo.record_event(contact_id, event_type, payload)
        if event_type == "replied" and self.notifier:
            self.notifier.notify(f"Lead replied: contact #{contact_id}")
        log("webhook.processed", provider=provider, contact_id=contact_id, event_type=event_type)
        return event_type

    def process_file(self, provider: str, path: Path) -> str:
        return self.process_payload(provider, json.loads(path.read_text(encoding="utf-8")))


def _extract_contact_id(payload: dict[str, Any]) -> int | None:
    for path in (
        ("contact_id",),
        ("data", "contact_id"),
        ("data", "metadata", "contact_id"),
        ("data", "tags", "contact_id"),
        ("metadata", "contact_id"),
        ("tags", "contact_id"),
    ):
        value: Any = payload
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if value:
            return int(value)
    tags = payload.get("tags") or payload.get("data", {}).get("tags")
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, dict) and tag.get("name") == "contact_id" and tag.get("value"):
                return int(tag["value"])
    return None


def _extract_event_type(provider: str, payload: dict[str, Any]) -> str:
    raw = str(payload.get("type") or payload.get("event") or payload.get("event_type") or "").lower()
    if "bounce" in raw:
        return "bounced"
    if "complain" in raw:
        return "complained"
    if "suppress" in raw:
        return "suppressed"
    if "delivery_delayed" in raw or "delayed" in raw:
        return "delivery_delayed"
    if "fail" in raw:
        return "failed"
    if "unsubscribe" in raw:
        return "unsubscribed"
    if "reply" in raw:
        return "replied"
    if "click" in raw:
        return "clicked"
    if "open" in raw:
        return "opened"
    if "deliver" in raw:
        return "delivered"
    return raw or "opened"


def _extract_message_id(payload: dict[str, Any]) -> str | None:
    for path in (
        ("email_id",),
        ("message_id",),
        ("id",),
        ("data", "email_id"),
        ("data", "message_id"),
        ("data", "id"),
        ("data", "email", "id"),
    ):
        value: Any = payload
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if value:
            return str(value)
    return None


__all__ = ["WebhookService", "_extract_contact_id", "_extract_event_type", "_extract_message_id"]
