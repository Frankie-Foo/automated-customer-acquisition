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
from ..mailbox_accounts import sender_identity_user, sender_transport_for_user
from ..outbound_identity import outbound_sender, signed_reply_address
from ..outreach_copy import (
    clean_public_research_item,
    contains_internal_outreach_data,
    customer_visible_contact,
    customer_visible_source_context,
)
from ..outreach_guard import send_readiness, sleep_between_sends, validate_email_body
from ..rendering import open_pixel_url, render_string, render_template, unsubscribe_url
from ..sender_pool import SenderPoolManager
from .pdca import LeadWorkflowService


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
        contact = self.repo.get_private_contact_for_user(contact_id, user) if user else self.repo.get_contact(contact_id)
        if not contact:
            raise ValueError("Contact not found or not claimed")
        sender_user = sender_identity_user(self.repo, contact, user)
        if mode == "custom":
            result = {
                "subject": custom_subject or f"Quick question about {contact.get('company_name') or 'your business'}",
                "body": _normalize_sender_signature(
                    custom_body or "",
                    sender_user,
                    fallback_name=self.config.sender.get("name", ""),
                    unsubscribe_value="{{unsubscribe_url}}",
                )
                if custom_body
                else "",
            }
            self._save_draft(contact, result, mode=mode, user=user)
            return result
        draft = self._ai_draft(contact, user=sender_user)
        result = {
            "subject": draft["subject"],
            "body": _normalize_sender_signature(
                draft["body"],
                sender_user,
                fallback_name=self.config.sender.get("name", ""),
                unsubscribe_value="{{unsubscribe_url}}",
            ),
        }
        self._save_draft(contact, result, mode=mode, user=user)
        return result

    def send(self, contact_id: int, *, subject: str, body: str, mode: str = "custom", user: dict[str, Any] | None = None) -> dict[str, Any]:
        contact = self.repo.get_private_contact_for_user(contact_id, user) if user else self.repo.get_contact(contact_id)
        if not contact:
            raise ValueError("Contact not found or not claimed")
        sender_user = sender_identity_user(self.repo, contact, user)
        sender_user_id = int(sender_user["id"]) if sender_user else None
        actor_user_id = int(user["id"]) if user else None
        readiness = send_readiness(contact)
        if not readiness["ok"]:
            raise ValueError(f"Contact is not ready to send: {', '.join(readiness['reasons'])}")
        approved = None
        if hasattr(self.repo, "get_latest_email_draft"):
            approved = self.repo.get_latest_email_draft(contact_id, user_id=int(user["id"]) if user else None)
            if not approved or approved.get("status") != "approved":
                raise ValueError("Email draft must be approved before sending")
            if approved.get("subject") != subject or approved.get("body") != body:
                raise ValueError("Email content changed after approval; approve the draft again")
        sender_pool = SenderPoolManager(self.config, self.repo)
        transport_sender = sender_pool.pick_sender()
        transport_sender, smtp_config = sender_transport_for_user(
            self.config,
            sender_user,
            transport_sender,
        )
        sender = outbound_sender(self.config, sender_user, transport_sender)
        api_key = self.config.apis.get(f"{transport_sender.get('provider', 'resend')}_key", "")
        mailer = MailClient(
            transport_sender.get("provider", "resend"),
            api_key,
            sender,
            smtp_config=smtp_config,
        )
        base_url = self.config.raw.get("app", {}).get("public_base_url", "http://127.0.0.1:8765")
        tracking_secret = _tracking_secret(self.config)
        step = int(contact.get("sequence_step") or 0) + 1
        reply_to = signed_reply_address(
            self.config,
            contact_id=int(contact["id"]),
            user_id=sender_user_id,
            sequence_step=step,
        ) or _reply_to_email(sender_user) or sender.get("email")
        values = {
            **contact,
            "sender_name": _sender_signature_name(sender_user, sender.get("name", "")),
            "sender_signature": _sender_signature(sender_user, sender.get("name", "")),
            "unsubscribe_url": unsubscribe_url(contact, base_url, tracking_secret),
            "account_context": _account_context(contact),
            "seed_reason": _source_context(contact).get("seed_reason", ""),
            "seed_category": _source_context(contact).get("seed_category", ""),
        }
        text = render_string(body, values)
        text = _normalize_sender_signature(text, sender_user, fallback_name=sender.get("name", ""), unsubscribe_value=values["unsubscribe_url"])
        if "Unsubscribe:" not in text:
            text = f"{text.rstrip()}\n\nUnsubscribe: {values['unsubscribe_url']}"
        validate_email_body(subject, text, min_chars=60)
        html_body = "<br>".join(html.escape(line) for line in text.splitlines())
        html_body += f'<img src="{open_pixel_url(contact, step, base_url, tracking_secret)}" width="1" height="1" alt="" />'
        idempotency_key = f"contact-{contact['id']}-step-{step}"
        attempt = self.repo.reserve_send_attempt(
            int(contact["id"]),
            step,
            user_id=sender_user_id,
            provider=str(transport_sender.get("provider") or "resend"),
            sender_email=sender.get("email"),
            idempotency_key=idempotency_key,
        )
        if not attempt or not attempt.get("reserved"):
            raise RuntimeError(f"send_step_already_{(attempt or {}).get('status') or 'reserved'}")
        try:
            message_id = mailer.send(
                contact["email"],
                subject,
                html_body,
                text,
                metadata={"contact_id": contact["id"], "sequence_step": step, "mode": mode, "user_id": sender_user_id},
                reply_to=reply_to,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            self.repo.finish_send_attempt(int(contact["id"]), step, error=str(exc)[:1000])
            if approved and hasattr(self.repo, "record_outreach_message"):
                self.repo.record_outreach_message(
                    contact_id=int(contact["id"]),
                    user_id=sender_user_id,
                    draft_id=int(approved["id"]),
                    channel="email",
                    sequence_step=step,
                    subject=subject,
                    body=body,
                    status="failed",
                    provider=str(transport_sender.get("provider") or "resend"),
                    error=str(exc)[:1000],
                )
            raise
        metadata = {
            "dry_run": sender.get("dry_run", True),
            "mode": mode,
            "sender_id": transport_sender.get("id"),
            "sender_email": sender.get("email"),
            "transport_sender_email": transport_sender.get("email"),
            "reply_to_email": reply_to,
            "reply_notification_email": _reply_to_email(sender_user),
            "user_id": sender_user_id,
            "actor_user_id": actor_user_id,
        }
        recorded = self.repo.record_manual_sent(contact["id"], step, subject, message_id, metadata)
        self.repo.finish_send_attempt(int(contact["id"]), step, message_id=message_id)
        self.repo.mark_latest_email_draft_sent(int(contact["id"]), user_id=int(user["id"]) if user else None)
        provider = str(transport_sender.get("provider") or "resend")
        if hasattr(self.repo, "record_outreach_message"):
            self.repo.record_outreach_message(
                contact_id=int(contact["id"]),
                user_id=sender_user_id,
                draft_id=int(approved["id"]) if approved else None,
                channel="email",
                sequence_step=step,
                subject=subject,
                body=body,
                status="sent",
                provider=provider,
                provider_message_id=message_id,
                metadata=metadata,
            )
            self.repo.update_outreach_message_event(
                provider=provider,
                provider_message_id=message_id,
                event_type="sent",
            )
        if hasattr(self.repo, "record_interaction"):
            self.repo.record_interaction(
                contact_id=int(contact["id"]),
                user_id=sender_user_id,
                interaction_type="email_sent",
                direction="outbound",
                channel="email",
                subject=subject,
                content=text,
                outcome="sent",
                source_ref=message_id,
                metadata=metadata,
            )
        if hasattr(self.repo, "close_open_followup_tasks"):
            self.repo.close_open_followup_tasks(int(contact["id"]))
            LeadWorkflowService(self.repo).ensure_next_task(
                int(contact["id"]),
                owner_user_id=sender_user_id or contact.get("owner_user_id"),
            )
        if recorded:
            sender_pool.record_send(transport_sender)
        return {
            "sent": bool(recorded),
            "contact_id": contact_id,
            "step": step,
            "message_id": message_id,
            "sender_email": sender.get("email"),
            "reply_to_email": reply_to,
        }

    def _save_draft(self, contact: dict[str, Any], draft: dict[str, str], *, mode: str, user: dict[str, Any] | None) -> None:
        if not hasattr(self.repo, "save_email_draft"):
            return
        research = self.repo.get_contact_research(int(contact["id"])) or {}
        saved = self.repo.save_email_draft(
            int(contact["id"]),
            user_id=int(user["id"]) if user else None,
            sequence_step=int(contact.get("sequence_step") or 0) + 1,
            mode=mode,
            subject=draft.get("subject") or "",
            body=draft.get("body") or "",
            research_snapshot={
                "summary": research.get("summary"),
                "sources": (research.get("sources") or [])[:6],
                "researched_at": str(research.get("researched_at") or ""),
            },
        )
        if saved and hasattr(self.repo, "record_outreach_message"):
            research_snapshot = saved.get("research_snapshot") if isinstance(saved.get("research_snapshot"), dict) else {}
            self.repo.record_outreach_message(
                contact_id=int(contact["id"]),
                user_id=int(user["id"]) if user else None,
                draft_id=int(saved["id"]),
                channel="email",
                sequence_step=int(saved.get("sequence_step") or 1),
                subject=saved.get("subject") or "",
                body=saved.get("body") or "",
                language=str(contact.get("language") or "en"),
                ai_model=str(self.config.raw.get("llm", {}).get("provider") or "fallback"),
                personalization_evidence=list(research_snapshot.get("sources") or []),
                status="draft",
                metadata={"mode": mode},
            )
        if saved and hasattr(self.repo, "close_open_followup_tasks") and hasattr(self.repo, "ensure_followup_task"):
            self.repo.close_open_followup_tasks(int(contact["id"]))
            owner_user_id = int(user["id"]) if user else contact.get("owner_user_id")
            self.repo.ensure_followup_task(
                contact_id=int(contact["id"]),
                assigned_user_id=owner_user_id,
                created_by_user_id=owner_user_id,
                task_type="review_draft",
                priority="normal",
                title=f"审核首封邮件：{_contact_name(contact)}",
                description="检查客户事实、主题、正文和收件邮箱后确认发送。",
                due_at=(datetime.now(UTC) + timedelta(hours=24)).isoformat(),
                trigger_rule="first_touch_review",
                metadata={"draft_id": saved["id"], "generated_by": "email_draft"},
            )

    def _ai_draft(self, contact: dict[str, Any], *, user: dict[str, Any] | None = None) -> dict[str, str]:
        copy_contact = customer_visible_contact(contact)
        fallback = self._fallback_draft(copy_contact, user=user)
        llm_cfg = self.config.raw.get("llm", {})
        provider = llm_cfg.get("provider", "deepseek")
        api_key = self.config.apis.get(f"{provider}_key", "") or self.config.apis.get("openai_key", "")
        if not api_key:
            return fallback
        insights = build_customer_profile(copy_contact)
        source_context = _source_context(copy_contact)
        account_context = _account_context(copy_contact)
        framework = insights.get("email_framework") if isinstance(insights.get("email_framework"), dict) else outreach_framework(copy_contact)
        pain_strategy = insights.get("pain_point_strategy") if isinstance(insights.get("pain_point_strategy"), dict) else {}
        followup_plan = insights.get("followup_plan") if isinstance(insights.get("followup_plan"), list) else []
        research = self.repo.get_contact_research(int(contact["id"])) or {}
        research_sources = []
        for item in (research.get("sources") or [])[:6]:
            cleaned = clean_public_research_item(item)
            if cleaned:
                research_sources.append({"index": len(research_sources) + 1, **cleaned})
        prompt = (
            "You are a B2B overseas sales email assistant. Generate one concise English email from only the provided facts. "
            "Output strict JSON only with fields: subject, body. Body must be plain text, 80-140 words, natural, and specific. "
            "Do not invent revenue, funding, customer names, case studies, news, meetings, or product claims. "
            "You may use at most one current signal from research_sources, only when its title and snippet directly support the wording. "
            "Treat undated or ambiguous sources as weak evidence and phrase them as an observation, not a confirmed business fact. "
            "Use this fixed pain-led five-part structure without headings: "
            "1) state the observed account signal or research reason, "
            "2) name the likely business pain as a hypothesis, not a fact, "
            "3) connect Vertu to that pain with a practical channel value, "
            "4) ask one low-barrier qualification question, "
            "5) close briefly. "
            "Avoid generic claims such as 'we are a leading brand' or 'high quality and good price'. "
            "Never expose CRM fields, lead scores, verification status, follow-up status, owners, source IDs, or internal notes. "
            f"Recipient: {copy_contact.get('first_name')} {copy_contact.get('last_name')}; role: {copy_contact.get('job_title')}; "
            f"company: {copy_contact.get('company_name')}; industry: {copy_contact.get('industry')}; location: {copy_contact.get('location')}; "
            f"approved public context: {json.dumps(source_context, ensure_ascii=False)}; account context sentence: {account_context}; "
            f"five-part framework: {json.dumps(framework, ensure_ascii=False)}; "
            f"pain point strategy: {json.dumps(pain_strategy, ensure_ascii=False)}; "
            f"14-day follow-up plan: {json.dumps(followup_plan, ensure_ascii=False)}; "
            f"research_sources: {json.dumps(research_sources, ensure_ascii=False)}; "
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
            if contains_internal_outreach_data(subject) or contains_internal_outreach_data(body):
                log("email_draft.rejected_internal_data", contact_id=contact.get("id"))
                return fallback
            return {"subject": subject, "body": body}
        except Exception as exc:
            log("email_draft.failed", contact_id=contact.get("id"), error=str(exc))
            return fallback

    def _fallback_draft(self, contact: dict[str, Any], *, user: dict[str, Any] | None = None) -> dict[str, str]:
        contact = customer_visible_contact(contact)
        company = contact.get("company_name") or "your business"
        first = contact.get("first_name") or "there"
        profile = build_customer_profile(contact)
        framework = profile.get("email_framework", outreach_framework(contact))
        strategy = profile.get("pain_point_strategy") or {}
        context = _source_context(contact)
        category = context.get("seed_category") or contact.get("industry") or "premium retail/distribution"
        role = contact.get("job_title") or "your team"
        match = strategy.get("message_hook") or framework.get("business_match") or _fallback_opening(contact)
        if not match or match.startswith("Reference the recipient"):
            match = f"I noticed {company} is relevant to {category}, and your role as {role} looks close to channel or commercial decisions."
        pain = strategy.get("suspected_pain") or f"For partners in {category}, the challenge is usually finding premium categories that add margin without adding heavy operational complexity."
        question = strategy.get("question_to_ask") or f"Would it be useful to explore whether Vertu could fit {company}'s current customer base?"
        value = (
            framework.get("our_value")
            or "Vertu is a premium mobile and luxury technology brand for selective high-end retail and distributor channels."
        )
        subject = f"Possible Vertu channel fit for {company}"
        body = (
            f"Hi {first},\n\n"
            f"{match}\n\n"
            f"My guess is that {pain[0].lower() + pain[1:] if pain else 'the right premium category must be easy to explain and low-friction to test.'}\n\n"
            f"{value} The practical angle is a selective, lightweight cooperation discussion rather than a heavy proposal upfront.\n\n"
            f"{question} A brief reply is enough to tell me whether this is relevant.\n\n"
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
        sender_user = sender_identity_user(self.repo, contact, user)
        sender_user_id = int(sender_user["id"]) if sender_user else None
        actor_user_id = int(user["id"]) if user else None
        readiness = send_readiness(contact)
        if not readiness["ok"]:
            log("send.skipped_quality_gate", contact_id=contact.get("id"), email=contact.get("email"), reasons=readiness["reasons"], score=readiness["score"])
            return False
        step_cfg = self._next_step_config(contact)
        if not step_cfg or not self._step_due(contact, step_cfg):
            return False
        sender_pool = SenderPoolManager(self.config, self.repo)
        transport_sender = sender_pool.pick_sender()
        transport_sender, smtp_config = sender_transport_for_user(
            self.config,
            sender_user,
            transport_sender,
        )
        sender = outbound_sender(self.config, sender_user, transport_sender)
        api_key = self.config.apis.get(f"{transport_sender.get('provider', 'resend')}_key", "")
        mailer = MailClient(
            transport_sender.get("provider", "resend"),
            api_key,
            sender,
            smtp_config=smtp_config,
        )
        subject = render_string(step_cfg["subject"], contact)
        base_url = self.config.raw.get("app", {}).get("public_base_url", "http://127.0.0.1:8765")
        tracking_secret = _tracking_secret(self.config)
        values = {
            **contact,
            "sender_name": _sender_signature_name(sender_user, sender.get("name", "")),
            "sender_signature": _sender_signature(sender_user, sender.get("name", "")),
            "unsubscribe_url": unsubscribe_url(contact, base_url, tracking_secret),
            "account_context": _account_context(contact),
            "seed_reason": _source_context(contact).get("seed_reason", ""),
            "seed_category": _source_context(contact).get("seed_category", ""),
            "ai_opener": ai.opener(contact) if step_cfg.get("ai_opener") else "",
        }
        template = self.config.root_dir / step_cfg["body_template"]
        text, html_body = render_template(template, values)
        text = _normalize_sender_signature(text, sender_user, fallback_name=sender.get("name", ""), unsubscribe_value=values["unsubscribe_url"])
        html_body = "<br>".join(html.escape(line) for line in text.splitlines())
        validate_email_body(subject, text)
        html_body += f'<img src="{open_pixel_url(contact, int(step_cfg["step"]), base_url, tracking_secret)}" width="1" height="1" alt="" />'
        step = int(step_cfg["step"])
        reply_to = signed_reply_address(
            self.config,
            contact_id=int(contact["id"]),
            user_id=sender_user_id,
            sequence_step=step,
        ) or _reply_to_email(sender_user) or sender.get("email")
        idempotency_key = f"contact-{contact['id']}-step-{step}"
        attempt = self.repo.reserve_send_attempt(
            int(contact["id"]),
            step,
            user_id=sender_user_id,
            provider=str(transport_sender.get("provider") or "resend"),
            sender_email=sender.get("email"),
            idempotency_key=idempotency_key,
        )
        if not attempt or not attempt.get("reserved"):
            log("send.skipped_duplicate", contact_id=contact.get("id"), step=step, status=(attempt or {}).get("status"))
            return False
        try:
            message_id = mailer.send(
                contact["email"],
                subject,
                html_body,
                text,
                metadata={"contact_id": contact["id"], "sequence_step": step, "user_id": sender_user_id},
                reply_to=reply_to,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            self.repo.finish_send_attempt(int(contact["id"]), step, error=str(exc)[:1000])
            raise
        metadata = {
            "dry_run": sender.get("dry_run", True),
            "sender_id": transport_sender.get("id"),
            "sender_email": sender.get("email"),
            "transport_sender_email": transport_sender.get("email"),
            "reply_to_email": reply_to,
            "reply_notification_email": _reply_to_email(sender_user),
            "user_id": sender_user_id,
            "actor_user_id": actor_user_id,
        }
        sent = self.repo.record_sent(contact["id"], step, subject, message_id, metadata)
        self.repo.finish_send_attempt(int(contact["id"]), step, message_id=message_id)
        if sent:
            sender_pool.record_send(transport_sender)
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
    return customer_visible_source_context(contact)


def _contact_name(contact: dict[str, Any]) -> str:
    person = " ".join(str(contact.get(key) or "").strip() for key in ("first_name", "last_name")).strip()
    return person or str(contact.get("company_name") or "客户")


def _account_context(contact: dict[str, Any]) -> str:
    context = _source_context(contact)
    parts = []
    if context.get("seed_category"):
        parts.append(f"category: {context['seed_category']}")
    if context.get("seed_location"):
        parts.append(f"market: {context['seed_location']}")
    signal = context.get("public_signal") or context.get("seed_reason")
    if signal:
        parts.append(f"public signal: {signal}")
    return "; ".join(parts)


def _fallback_opening(contact: dict[str, Any]) -> str:
    company = contact.get("company_name") or "your company"
    context = _source_context(contact)
    signal = context.get("public_signal") or context.get("seed_reason")
    if signal:
        return f"I noticed this public signal about {company}: {signal}"
    if context.get("seed_category"):
        return f"I noticed {company} is relevant to {context['seed_category']} and thought this might be worth a quick conversation."
    return f"I noticed your work as {contact.get('job_title') or 'a leader'} at {company} and thought this might be relevant."


def _reply_to_email(user: dict[str, Any] | None) -> str | None:
    value = str((user or {}).get("reply_to_email") or "").strip()
    if "@" not in value or " " in value:
        return None
    return value


def _tracking_secret(config: AppConfig) -> str:
    app_secret = str(config.raw.get("app", {}).get("tracking_signing_secret") or "").strip()
    webhook_secret = str(config.raw.get("webhooks", {}).get("resend_secret") or "").strip()
    secret = app_secret or webhook_secret
    if len(secret) < 24:
        raise RuntimeError("TRACKING_SIGNING_SECRET must contain at least 24 characters")
    return secret


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
