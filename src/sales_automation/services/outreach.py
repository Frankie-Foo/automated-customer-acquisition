from __future__ import annotations

import html
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from ..clients import LLMClient, MailClient
from ..config import AppConfig
from ..customer_intelligence import build_customer_profile, outreach_framework
from ..db import Repository
from ..logging_utils import log
from ..outreach_guard import send_readiness, sleep_between_sends, validate_email_body
from ..rendering import open_pixel_url, render_string, render_template, unsubscribe_url
from ..sender_pool import SenderPoolManager


class PersonalizedEmailService:
    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo

    def draft(
        self,
        contact_id: int,
        *,
        mode: str = "ai",
        custom_subject: str | None = None,
        custom_body: str | None = None,
        user: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        contact = self.repo.get_contact(contact_id)
        if not contact:
            raise ValueError("Contact not found")
        if mode == "custom":
            return {
                "subject": custom_subject or f"Quick question about {contact.get('company_name') or 'your business'}",
                "body": _normalize_sender_signature(
                    custom_body or "",
                    user,
                    fallback_name=self.config.sender.get("name", ""),
                    unsubscribe_value="{{unsubscribe_url}}",
                )
                if custom_body
                else "",
            }
        draft = self._ai_draft(contact, user=user)
        return {
            "subject": draft["subject"],
            "body": _normalize_sender_signature(
                draft["body"],
                user,
                fallback_name=self.config.sender.get("name", ""),
                unsubscribe_value="{{unsubscribe_url}}",
            ),
        }

    def send(self, contact_id: int, *, subject: str, body: str, mode: str = "custom", user: dict[str, Any] | None = None) -> dict[str, Any]:
        contact = self.repo.get_contact(contact_id)
        if not contact:
            raise ValueError("Contact not found")
        readiness = send_readiness(contact)
        if not readiness["ok"]:
            raise ValueError(f"Contact is not ready to send: {', '.join(readiness['reasons'])}")
        sender_pool = SenderPoolManager(self.config, self.repo)
        sender = sender_pool.pick_sender()
        api_key = self.config.apis.get(f"{sender.get('provider', 'resend')}_key", "")
        mailer = MailClient(sender.get("provider", "resend"), api_key, sender)
        reply_to = _reply_to_email(user)
        base_url = self.config.raw.get("app", {}).get("public_base_url", "http://127.0.0.1:8765")
        step = int(contact.get("sequence_step") or 0) + 1
        values = {
            **contact,
            "sender_name": _sender_signature_name(user, sender.get("name", "")),
            "sender_signature": _sender_signature(user, sender.get("name", "")),
            "unsubscribe_url": unsubscribe_url(contact, base_url),
            "account_context": _account_context(contact),
            "seed_reason": _source_context(contact).get("seed_reason", ""),
            "seed_category": _source_context(contact).get("seed_category", ""),
        }
        text = render_string(body, values)
        text = _normalize_sender_signature(text, user, fallback_name=sender.get("name", ""), unsubscribe_value=values["unsubscribe_url"])
        if "Unsubscribe:" not in text:
            text = f"{text.rstrip()}\n\nUnsubscribe: {values['unsubscribe_url']}"
        validate_email_body(subject, text, min_chars=60)
        html_body = "<br>".join(html.escape(line) for line in text.splitlines())
        html_body += f'<img src="{open_pixel_url(contact, step, base_url)}" width="1" height="1" alt="" />'
        message_id = mailer.send(
            contact["email"],
            subject,
            html_body,
            text,
            metadata={"contact_id": contact["id"], "sequence_step": step, "mode": mode, "user_id": user.get("id") if user else None},
            reply_to=reply_to,
        )
        metadata = {
            "dry_run": sender.get("dry_run", True),
            "mode": mode,
            "sender_id": sender.get("id"),
            "sender_email": sender.get("email"),
            "reply_to_email": reply_to,
            "user_id": user.get("id") if user else None,
        }
        recorded = self.repo.record_manual_sent(contact["id"], step, subject, message_id, metadata)
        if recorded:
            sender_pool.record_send(sender)
        return {"sent": bool(recorded), "contact_id": contact_id, "step": step, "message_id": message_id}

    def _ai_draft(self, contact: dict[str, Any], *, user: dict[str, Any] | None = None) -> dict[str, str]:
        fallback = self._fallback_draft(contact, user=user)
        llm_cfg = self.config.raw.get("llm", {})
        provider = llm_cfg.get("provider", "deepseek")
        api_key = self.config.apis.get(f"{provider}_key", "") or self.config.apis.get("openai_key", "")
        if not api_key:
            return fallback
        insights = contact.get("profile_insights") if isinstance(contact.get("profile_insights"), dict) else {}
        if not insights:
            insights = build_customer_profile(contact)
        source_context = _source_context(contact)
        account_context = _account_context(contact)
        framework = insights.get("email_framework") if isinstance(insights.get("email_framework"), dict) else outreach_framework(contact)
        activities = self.repo.list_lifecycle_activities(int(contact["id"]), limit=5)
        history = "\n".join(f"- {item.get('content')}" for item in activities if item.get("content"))
        prompt = (
            "You are a B2B overseas sales email assistant. Generate one concise English email from only the provided facts. "
            "Output strict JSON only with fields: subject, body. Body must be plain text, 80-140 words, natural, and specific. "
            "Do not invent revenue, funding, customer names, case studies, news, meetings, or product claims. "
            "Use this fixed five-part structure without headings: 1) state the reason for writing, 2) match the recipient's business, "
            "3) explain Vertu's relevant value, 4) make a low-barrier ask, 5) close briefly. "
            f"Recipient: {contact.get('first_name')} {contact.get('last_name')}; role: {contact.get('job_title')}; "
            f"company: {contact.get('company_name')}; industry: {contact.get('industry')}; location: {contact.get('location')}; "
            f"lifecycle stage: {contact.get('lifecycle_stage')}; profile insights: {insights}; recent notes: {history}; "
            f"imported account context: {json.dumps(source_context, ensure_ascii=False)}; account context sentence: {account_context}; "
            f"five-part framework: {json.dumps(framework, ensure_ascii=False)}; "
            f"sender: {self.config.sender.get('name')}."
        )
        try:
            client = LLMClient(
                api_key,
                provider=provider,
                base_url=llm_cfg.get("base_url", "https://api.deepseek.com"),
                model=llm_cfg.get("model", "deepseek-chat"),
            )
            data = client.http.request(
                "POST",
                f"{client.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json_body={
                    "model": client.model,
                    "messages": [
                        {"role": "system", "content": "You only output strict JSON for a B2B sales email draft."},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 420,
                    "temperature": 0.35,
                },
            )
            text = str(data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
            if text.startswith("```"):
                text = text.strip("`")
                if text.lower().startswith("json"):
                    text = text[4:].strip()
            draft = json.loads(text)
            subject = str(draft.get("subject") or fallback["subject"])[:160]
            body = str(draft.get("body") or fallback["body"])[:3000]
            return {"subject": subject, "body": body}
        except Exception as exc:
            log("email_draft.failed", contact_id=contact.get("id"), error=str(exc))
            return fallback

    def _fallback_draft(self, contact: dict[str, Any], *, user: dict[str, Any] | None = None) -> dict[str, str]:
        company = contact.get("company_name") or "your business"
        first = contact.get("first_name") or "there"
        profile = build_customer_profile(contact)
        framework = profile.get("email_framework", outreach_framework(contact))
        context = _source_context(contact)
        category = context.get("seed_category") or contact.get("industry") or "premium retail/distribution"
        role = contact.get("job_title") or "your team"
        match = framework.get("business_match") or _fallback_opening(contact)
        if not match or match.startswith("Reference the recipient"):
            match = f"I noticed {company} is relevant to {category}, and your role as {role} looks close to channel or commercial decisions."
        value = (
            framework.get("our_value")
            or "Vertu is a premium mobile and luxury technology brand for selective high-end retail and distributor channels."
        )
        subject = f"Possible Vertu channel fit for {company}"
        body = (
            f"Hi {first},\n\n"
            f"{match}\n\n"
            f"{value} We are looking for partners where the customer base already values high-end products, service, and differentiated retail experiences.\n\n"
            f"If {company} is exploring new premium categories or partner brands, I can send a short note on where Vertu may fit and what a lightweight cooperation model could look like.\n\n"
            "Would it be worth a brief reply to see if this is relevant?\n\n"
            f"{_sender_signature(user, self.config.sender.get('name', ''))}\n\n"
            "Unsubscribe: {{unsubscribe_url}}"
        )
        return {"subject": subject, "body": body}


class OutreachService:
    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo

    def send_due(self, limit: int, *, user: dict[str, Any] | None = None) -> int:
        ai = self._ai_client()
        sent = 0
        attempted = 0
        for contact in self.repo.due_for_sending(limit, user=user):
            if attempted:
                sleep_between_sends(self.config)
            attempted += 1
            if self._send_contact(contact, ai, user=user):
                sent += 1
        log("send.completed", sent=sent)
        return sent

    def send_contact(self, contact_id: int, *, user: dict[str, Any] | None = None) -> bool:
        ai = self._ai_client()
        contact = self.repo.due_contact_for_sending(contact_id, user=user)
        if not contact:
            return False
        sent = self._send_contact(contact, ai, user=user)
        log("send.contact_completed", contact_id=contact_id, sent=sent)
        return sent

    def _ai_client(self) -> LLMClient:
        llm_cfg = self.config.raw.get("llm", {})
        provider = llm_cfg.get("provider", "deepseek")
        api_key = self.config.apis.get(f"{provider}_key", "") or self.config.apis.get("openai_key", "")
        return LLMClient(
            api_key,
            provider=provider,
            base_url=llm_cfg.get("base_url", "https://api.deepseek.com"),
            model=llm_cfg.get("model", "deepseek-chat"),
        )

    def _send_contact(self, contact: dict[str, Any], ai: LLMClient, *, user: dict[str, Any] | None = None) -> bool:
        fresh = self.repo.due_contact_for_sending(int(contact["id"]), user=user)
        if not fresh:
            log("send.skipped_not_due", contact_id=contact.get("id"))
            return False
        contact = fresh
        readiness = send_readiness(contact)
        if not readiness["ok"]:
            log("send.skipped_quality_gate", contact_id=contact.get("id"), email=contact.get("email"), reasons=readiness["reasons"], score=readiness["score"])
            return False
        step_cfg = self._next_step_config(contact)
        if not step_cfg or not self._step_due(contact, step_cfg):
            return False
        sender_pool = SenderPoolManager(self.config, self.repo)
        sender = sender_pool.pick_sender()
        api_key = self.config.apis.get(f"{sender.get('provider', 'resend')}_key", "")
        mailer = MailClient(sender.get("provider", "resend"), api_key, sender)
        reply_to = _reply_to_email(user)
        subject = render_string(step_cfg["subject"], contact)
        base_url = self.config.raw.get("app", {}).get("public_base_url", "http://127.0.0.1:8765")
        values = {
            **contact,
            "sender_name": _sender_signature_name(user, sender.get("name", "")),
            "sender_signature": _sender_signature(user, sender.get("name", "")),
            "unsubscribe_url": unsubscribe_url(contact, base_url),
            "account_context": _account_context(contact),
            "seed_reason": _source_context(contact).get("seed_reason", ""),
            "seed_category": _source_context(contact).get("seed_category", ""),
            "ai_opener": ai.opener(contact) if step_cfg.get("ai_opener") else "",
        }
        template = self.config.root_dir / step_cfg["body_template"]
        text, html_body = render_template(template, values)
        text = _normalize_sender_signature(text, user, fallback_name=sender.get("name", ""), unsubscribe_value=values["unsubscribe_url"])
        html_body = "<br>".join(html.escape(line) for line in text.splitlines())
        validate_email_body(subject, text)
        html_body += f'<img src="{open_pixel_url(contact, int(step_cfg["step"]), base_url)}" width="1" height="1" alt="" />'
        message_id = mailer.send(
            contact["email"],
            subject,
            html_body,
            text,
            metadata={"contact_id": contact["id"], "sequence_step": step_cfg["step"], "user_id": user.get("id") if user else None},
            reply_to=reply_to,
        )
        metadata = {
            "dry_run": sender.get("dry_run", True),
            "sender_id": sender.get("id"),
            "sender_email": sender.get("email"),
            "reply_to_email": reply_to,
            "user_id": user.get("id") if user else None,
        }
        sent = self.repo.record_sent(contact["id"], int(step_cfg["step"]), subject, message_id, metadata)
        if sent:
            sender_pool.record_send(sender)
            log("send.sent", contact_id=contact["id"], step=step_cfg["step"], dry_run=sender.get("dry_run", True))
        return sent

    def _next_step_config(self, contact: dict[str, Any]) -> dict[str, Any] | None:
        next_step = int(contact.get("sequence_step") or 0) + 1
        for step in self.config.sequence:
            if int(step["step"]) == next_step:
                return step
        return None

    def _step_due(self, contact: dict[str, Any], step: dict[str, Any]) -> bool:
        if contact.get("status") == "queued":
            return True
        last = contact.get("last_contacted_at")
        if not last:
            return True
        if isinstance(last, str):
            last = datetime.fromisoformat(last)
        return datetime.now(UTC) >= last + timedelta(days=int(step.get("delay_days", 0)))


def _source_context(contact: dict[str, Any]) -> dict[str, str]:
    context = contact.get("source_context")
    if not isinstance(context, dict):
        return {}
    return {str(key): str(value).strip() for key, value in context.items() if str(value or "").strip()}


def _account_context(contact: dict[str, Any]) -> str:
    context = _source_context(contact)
    parts = []
    if context.get("seed_category"):
        parts.append(f"category: {context['seed_category']}")
    if context.get("seed_location"):
        parts.append(f"market: {context['seed_location']}")
    if context.get("seed_reason"):
        parts.append(f"research note: {context['seed_reason']}")
    return "; ".join(parts)


def _fallback_opening(contact: dict[str, Any]) -> str:
    company = contact.get("company_name") or "your company"
    context = _source_context(contact)
    if context.get("seed_reason"):
        return f"I noticed {company} in our account research: {context['seed_reason']}"
    if context.get("seed_category"):
        return f"I noticed {company} is relevant to {context['seed_category']} and thought this might be worth a quick conversation."
    return f"I noticed your work as {contact.get('job_title') or 'a leader'} at {company} and thought this might be relevant."


def _reply_to_email(user: dict[str, Any] | None) -> str | None:
    value = str((user or {}).get("reply_to_email") or "").strip()
    if "@" not in value or " " in value:
        return None
    return value


def _sender_signature_name(user: dict[str, Any] | None, fallback_name: str = "") -> str:
    value = str((user or {}).get("display_name") or (user or {}).get("username") or fallback_name or "Vertu").strip()
    return value or "Vertu"


def _sender_signature(user: dict[str, Any] | None, fallback_name: str = "") -> str:
    name = _sender_signature_name(user, fallback_name)
    signature_name = name if name.lower().endswith(" you") else f"{name} You"
    return f"Best regards,\n{signature_name}\nBD Manager Of Media East Region | VERTU"


_SIGNOFF_RE = re.compile(r"\n+(?:Best|Best regards|Regards),\s*\n(?:[^\n]*\n?){0,4}\s*$", re.IGNORECASE)


def _normalize_sender_signature(
    text: str,
    user: dict[str, Any] | None,
    *,
    fallback_name: str = "",
    unsubscribe_value: str | None = None,
) -> str:
    body = str(text or "").rstrip()
    unsubscribe = ""
    marker_index = body.lower().rfind("\nunsubscribe:")
    if marker_index >= 0:
        unsubscribe = body[marker_index:].strip()
        body = body[:marker_index].rstrip()
    elif unsubscribe_value:
        unsubscribe = f"Unsubscribe: {unsubscribe_value}"

    body = _SIGNOFF_RE.sub("", body).rstrip()
    parts = [part for part in [body, _sender_signature(user, fallback_name), unsubscribe] if part]
    return "\n\n".join(parts)


__all__ = ["OutreachService", "PersonalizedEmailService"]
