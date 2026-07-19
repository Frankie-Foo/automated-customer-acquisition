from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..clients import HttpClient, SlackClient
from ..db import Repository
from ..logging_utils import log
from ..outbound_identity import parse_signed_reply_route
from ..outreach_guard import annotate_delivery_payload
from .pdca import LeadWorkflowService

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)


class WebhookService:
    def __init__(self, repo: Repository, notifier: SlackClient | None = None, config: Any = None, http: HttpClient | None = None):
        self.repo = repo
        self.notifier = notifier
        self.config = config
        self.http = http or HttpClient()

    def process_payload(self, provider: str, payload: dict[str, Any]) -> str:
        event_type = _extract_event_type(provider, payload)
        if provider == "resend" and event_type == "replied":
            payload = self._hydrate_resend_received_email(payload)
        reply_route = parse_signed_reply_route(self.config, _extract_reply_recipients(payload)) if self.config else None
        contact_id = int(reply_route["contact_id"]) if reply_route and event_type == "replied" else _extract_contact_id(payload)
        if not contact_id:
            message_id = _extract_message_id(payload)
            if message_id:
                contact_id = self.repo.find_contact_id_by_message_id(message_id)
        if not contact_id and hasattr(self.repo, "find_contact_id_by_email"):
            lookup_email = _extract_sender_email(payload) if event_type == "replied" else _extract_recipient_email(payload)
            if lookup_email:
                contact_id = self.repo.find_contact_id_by_email(lookup_email)
        if not contact_id:
            raise ValueError("Webhook payload does not include contact_id metadata or a known message id")
        if event_type == "replied" and hasattr(self.repo, "route_inbound_reply"):
            route_state = self.repo.route_inbound_reply(contact_id, reply_route.get("user_id") if reply_route else None)
            payload = {
                **payload,
                "reply_route": reply_route,
                "reply_owner_user_id": route_state.get("owner_user_id"),
                "reply_assignment_pending": route_state.get("reply_assignment_pending", False),
            }
        payload = annotate_delivery_payload(event_type, payload)
        self.repo.record_event(contact_id, event_type, payload)
        message_id = _extract_message_id(payload)
        if message_id and hasattr(self.repo, "update_outreach_message_event"):
            self.repo.update_outreach_message_event(
                provider=provider,
                provider_message_id=message_id,
                event_type=event_type,
                error=str(payload.get("delivery_reason") or "")[:1000] or None,
            )
        contact = self.repo.get_contact(contact_id) if hasattr(self.repo, "get_contact") else None
        owner_user_id = contact.get("owner_user_id") if contact else None
        if event_type == "opened" and contact and hasattr(self.repo, "ensure_followup_task"):
            self.repo.ensure_followup_task(
                contact_id=contact_id,
                assigned_user_id=owner_user_id,
                created_by_user_id=owner_user_id,
                task_type="followup",
                priority="high",
                title=f"跟进已打开的客户 {_contact_name(contact)}",
                description="客户已打开但尚未回复，补充一个新的业务价值点，不要重复首封内容。",
                due_at=None,
                trigger_rule="opened_no_reply",
                metadata={"provider": provider, "message_id": message_id},
            )
        if event_type in {"replied", "bounced", "failed", "unsubscribed", "complained"} and contact:
            if hasattr(self.repo, "close_open_followup_tasks"):
                self.repo.close_open_followup_tasks(contact_id)
            LeadWorkflowService(self.repo).ensure_next_task(
                contact_id,
                owner_user_id=owner_user_id,
            )
        if event_type in {"replied", "bounced", "failed"} and hasattr(self.repo, "record_interaction"):
            self.repo.record_interaction(
                contact_id=contact_id,
                user_id=owner_user_id,
                interaction_type="email_reply" if event_type == "replied" else "email_delivery_failure",
                direction="inbound",
                channel="email",
                subject=_extract_subject(payload),
                content=_extract_message_text(payload),
                outcome=event_type,
                source_ref=message_id,
                metadata={"provider": provider},
            )
        if event_type == "replied" and hasattr(self.repo, "add_lifecycle_activity"):
            self.repo.add_lifecycle_activity(
                contact_id,
                lifecycle_stage="replied",
                activity_type="reply",
                title=_extract_subject(payload) or "Email reply",
                content=_extract_message_text(payload) or "Reply received",
                created_by=f"{provider}_webhook",
            )
        if event_type == "replied" and self.notifier:
            self.notifier.notify(f"Lead replied: contact #{contact_id}")
        log("webhook.processed", provider=provider, contact_id=contact_id, event_type=event_type)
        return event_type

    def _hydrate_resend_received_email(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        email_id = data.get("email_id")
        api_key = str(getattr(self.config, "apis", {}).get("resend_key") or "") if self.config else ""
        if not email_id or not api_key or data.get("text") or data.get("body"):
            return payload
        received = self.http.request(
            "GET",
            f"https://api.resend.com/emails/receiving/{email_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            retries=3,
        )
        reply_text = str(received.get("text") or _html_to_text(received.get("html")))[:10000]
        return {
            **payload,
            "data": {
                **data,
                "text": reply_text,
                "message_id": received.get("message_id") or data.get("message_id"),
            },
        }

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
    if "reply" in raw or "received" in raw:
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


def _extract_recipient_email(payload: dict[str, Any]) -> str | None:
    for path in (
        ("to",),
        ("recipient",),
        ("email",),
        ("data", "to"),
        ("data", "recipient"),
        ("data", "email"),
        ("data", "email", "to"),
    ):
        value: Any = payload
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        email = _first_email_value(value)
        if email:
            return email
    return None


def _extract_sender_email(payload: dict[str, Any]) -> str | None:
    for path in (
        ("from",),
        ("sender",),
        ("data", "from"),
        ("data", "sender"),
        ("data", "email", "from"),
    ):
        value: Any = payload
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        email = _first_email_value(value)
        if email:
            return email
    return None


def _first_email_value(value: Any) -> str | None:
    if isinstance(value, str) and "@" in value:
        match = EMAIL_RE.search(value)
        return match.group(0).lower() if match else value.strip().lower()
    if isinstance(value, dict):
        for key in ("email", "address"):
            item = value.get(key)
            if isinstance(item, str) and "@" in item:
                return item.strip().lower()
    if isinstance(value, list):
        for item in value:
            email = _first_email_value(item)
            if email:
                return email
    return None


def _extract_subject(payload: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    email = data.get("email") if isinstance(data.get("email"), dict) else {}
    for value in (payload.get("subject"), data.get("subject"), email.get("subject")):
        if value:
            return str(value)[:300]
    return ""


def _extract_message_text(payload: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    email = data.get("email") if isinstance(data.get("email"), dict) else {}
    for value in (payload.get("text"), payload.get("body"), data.get("text"), data.get("body"), email.get("text"), email.get("body")):
        if value:
            return str(value)[:10000]
    return ""


def _extract_reply_recipients(payload: dict[str, Any]) -> list[Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    email = data.get("email") if isinstance(data.get("email"), dict) else {}
    return [payload.get("to"), payload.get("recipient"), data.get("to"), data.get("recipient"), email.get("to")]


def _html_to_text(value: Any) -> str:
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", str(value or ""), flags=re.IGNORECASE)
    text = re.sub(r"</\s*(p|div|li|tr|h[1-6])\s*>", "\n", text, flags=re.IGNORECASE)
    return re.sub(r"<[^>]+>", "", text).strip()


def _contact_name(contact: dict[str, Any]) -> str:
    person = " ".join(str(contact.get(key) or "").strip() for key in ("first_name", "last_name")).strip()
    return person or str(contact.get("company_name") or "客户")


__all__ = ["WebhookService", "_extract_contact_id", "_extract_event_type", "_extract_message_id", "_extract_recipient_email", "_extract_sender_email"]
