from __future__ import annotations

import html
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..clients import MailClient
from ..config import AppConfig
from ..customer_intelligence import build_customer_profile, outreach_framework
from ..db import Repository
from ..llm_gateway import LLMGateway
from ..logging_utils import log
from ..mailbox_accounts import sales_mailbox, sender_identity_user, sender_transport_for_user
from ..outbound_identity import outbound_sender, signed_reply_address
from ..outbound_quality import review_email_copy
from ..outreach_copy import (
    clean_public_research_item,
    contains_internal_outreach_data,
    customer_visible_contact,
    customer_visible_source_context,
)
from ..outreach_guard import company_identity_issues, send_readiness, sleep_between_sends, validate_email_body
from ..rendering import build_html_body, open_pixel_url, render_string, render_template, unsubscribe_url
from ..sender_pool import SenderPoolManager
from .pdca import LeadWorkflowService
from .flywheel import DataFlywheelService
from .quality import OutboundQualityService


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
    ) -> dict[str, Any]:
        contact = self.repo.get_private_contact_for_user(contact_id, user) if user else self.repo.get_contact(contact_id)
        if not contact:
            raise ValueError("Contact not found or not claimed")
        issues = company_identity_issues(contact)
        if issues:
            raise ValueError("Company needs review: " + ", ".join(issues))
        sender_user = sender_identity_user(self.repo, contact, user)
        signature = _signature_profile(self.config, sender_user)
        quality_service = OutboundQualityService(self.repo)
        experiment = quality_service.experiment_assignment(
            contact_id=int(contact["id"]),
            owner_user_id=int(sender_user["id"]) if sender_user else contact.get("owner_user_id"),
        )
        if mode == "custom":
            result = {
                "subject": custom_subject or f"Quick question about {contact.get('company_name') or 'your business'}",
                "body": _normalize_sender_signature(
                    custom_body or "",
                    sender_user,
                    fallback_name=self.config.sender.get("name", ""),
                    signature=signature,
                    unsubscribe_value="{{unsubscribe_url}}",
                )
                if custom_body
                else "",
            }
            result["quality_review"] = quality_service.review_draft(
                result["subject"], result["body"], contact=contact
            )
            result["experiment"] = experiment
            self._save_draft(contact, result, mode=mode, user=user)
            return result
        draft = self._ai_draft(contact, user=sender_user, experiment=experiment)
        result = {
            "subject": draft["subject"],
            "body": _normalize_sender_signature(
                draft["body"],
                sender_user,
                fallback_name=self.config.sender.get("name", ""),
                signature=signature,
                unsubscribe_value="{{unsubscribe_url}}",
            ),
        }
        result["quality_review"] = quality_service.review_draft(
            result["subject"], result["body"], contact=contact
        )
        result["experiment"] = experiment
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
        quality_review = OutboundQualityService(self.repo).review_draft(subject, body, contact=contact)
        if quality_review["status"] == "blocked":
            codes = ", ".join(item["code"] for item in quality_review["blocking_issues"])
            raise ValueError(f"Email quality check failed: {codes}")
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
            "sender_signature": _sender_signature(sender_user, sender.get("name", ""), sender.get("signature")),
            "unsubscribe_url": unsubscribe_url(contact, base_url, tracking_secret),
            "account_context": _account_context(contact),
            "seed_reason": _source_context(contact).get("seed_reason", ""),
            "seed_category": _source_context(contact).get("seed_category", ""),
        }
        text = render_string(body, values)
        text = _normalize_sender_signature(text, sender_user, fallback_name=sender.get("name", ""), signature=sender.get("signature"), unsubscribe_value=values["unsubscribe_url"])
        if "Unsubscribe:" not in text:
            text = f"{text.rstrip()}\n\nUnsubscribe: {values['unsubscribe_url']}"
        validate_email_body(subject, text, min_chars=60)
        html_body = build_html_body(
            text,
            product_images=getattr(self.config, "product_images", {}),
            signature=sender.get("signature"),
        )
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
                attachments=_brand_attachments(self.config, sender.get("signature")),
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
                    quality_review=quality_review,
                    experiment_id=approved.get("experiment_id"),
                    experiment_variant=approved.get("experiment_variant"),
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
                quality_review=quality_review,
                experiment_id=approved.get("experiment_id") if approved else None,
                experiment_variant=approved.get("experiment_variant") if approved else None,
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

    def _save_draft(self, contact: dict[str, Any], draft: dict[str, Any], *, mode: str, user: dict[str, Any] | None) -> None:
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
            quality_review=draft.get("quality_review") or {},
            experiment_id=(draft.get("experiment") or {}).get("experiment_id"),
            experiment_variant=(draft.get("experiment") or {}).get("variant"),
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
                quality_review=saved.get("quality_review") or draft.get("quality_review") or {},
                experiment_id=saved.get("experiment_id"),
                experiment_variant=saved.get("experiment_variant"),
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

    def _ai_draft(
        self,
        contact: dict[str, Any],
        *,
        user: dict[str, Any] | None = None,
        experiment: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        copy_contact = customer_visible_contact(contact)
        research = self.repo.get_contact_research(int(contact["id"])) if hasattr(self.repo, "get_contact_research") else None
        research = research or {}
        segment = _account_segment(copy_contact, research)
        fallback = self._fallback_draft(copy_contact, user=user, research=research)
        gateway = LLMGateway(self.config, self.repo)
        if not gateway.can_generate(contact):
            return fallback
        insights = build_customer_profile(copy_contact)
        source_context = _source_context(copy_contact)
        account_context = _account_context(copy_contact)
        framework = insights.get("email_framework") if isinstance(insights.get("email_framework"), dict) else outreach_framework(copy_contact)
        pain_strategy = insights.get("pain_point_strategy") if isinstance(insights.get("pain_point_strategy"), dict) else {}
        followup_plan = insights.get("followup_plan") if isinstance(insights.get("followup_plan"), list) else []
        recipient_mandate = _recipient_mandate(copy_contact)
        flywheel = DataFlywheelService(self.config, self.repo).context_for_contact(copy_contact)
        research_sources = []
        for item in (research.get("sources") or [])[:6]:
            cleaned = clean_public_research_item(item)
            if cleaned:
                research_sources.append({"index": len(research_sources) + 1, **cleaned})
        prompt = (
            "You are a VERTU global-headquarters channel-development representative. Generate one concise English first-touch email from only the provided facts. "
            "The commercial purpose is to identify a credible local partner who could open and operate a VERTU boutique or develop selective local distribution. "
            "This email will be sent with VERTU product images displayed below the text, so you do not need to describe phones, watches, or wearables in the body. "
            "Frame the value as a differentiated luxury category and potential commercial upside for the partner's high-value customer base; never promise revenue, margin, returns, or an outcome. "
            "Output strict JSON only with fields: subject, body. Body must be plain text, 100-220 prospect-facing words before the signature, natural, specific, and easy to scan. "
            "Do not invent revenue, funding, customer names, case studies, news, meetings, or product claims. "
            "You may use at most one current signal from research_sources, only when its title and snippet directly support the wording. "
            "Treat undated or ambiguous sources as weak evidence and phrase them as an observation, not a confirmed business fact. "
            "Use a peer-to-peer commercial tone, not a sales script. Every sentence must add a fact, commercial hypothesis, or practical partner value. "
            "Use this structure without headings: direct sender introduction; one or two verified account facts; a clear commercial thesis explaining why the recipient's existing customer base or channel could fit VERTU; two or three concise cooperation routes when supported; one business rationale; exactly one low-friction next-step question. "
            "The CTA may offer a market-specific partnership deck or ask whether a short discussion is useful, but never manufacture travel plans, meetings, attachments, deadlines, scarcity, or urgency. "
            "Mention VERTU's broader luxury portfolio only when relevant, using only these approved categories: luxury smartphones, watches, jewelry, fine leather goods, and connected luxury products. "
            f"Use this approved brand introduction once: {_APPROVED_BRAND_INTRO} "
            f"Use this approved market intention once: {_market_intent(copy_contact, segment)} "
            f"State that VERTU's approved 2026 roadmap includes only: {', '.join(_APPROVED_2026_PORTFOLIO)}. Present it compactly and do not add claims, launch dates, specifications, or performance promises. Use 'return' only for India; for another verified country use 'expand in'; if the country is unknown, do not name a market. "
            "Personalize for the recipient's actual decision lens. A CEO/owner should receive a strategic growth case; a buyer a portfolio and customer-fit case; retail operations a store-format and service case; marketing a VIP activation case. Do not send different roles at the same company the same argument. "
            "Avoid generic claims such as 'we are a leading brand', 'exclusive opportunity', 'hope this email finds you well', or 'high quality and good price'. "
            "Never expose CRM fields, lead scores, verification status, follow-up status, owners, source IDs, or internal notes. "
            f"Recipient: {copy_contact.get('first_name')} {copy_contact.get('last_name')}; role: {copy_contact.get('job_title')}; "
            f"company: {copy_contact.get('company_name')}; industry: {copy_contact.get('industry')}; location: {copy_contact.get('location')}; "
            f"approved public context: {json.dumps(source_context, ensure_ascii=False)}; account context sentence: {account_context}; "
            f"five-part framework: {json.dumps(framework, ensure_ascii=False)}; "
            f"pain point strategy: {json.dumps(pain_strategy, ensure_ascii=False)}; "
            f"recipient decision lens: {json.dumps(recipient_mandate, ensure_ascii=False)}; "
            f"engagement context: sequence_step={int(copy_contact.get('sequence_step') or 0)}, status={str(copy_contact.get('status') or 'new')}, last_event={str(copy_contact.get('last_event_type') or 'none')}; "
            f"14-day follow-up plan: {json.dumps(followup_plan, ensure_ascii=False)}; "
            f"research_sources: {json.dumps(research_sources, ensure_ascii=False)}; "
            f"validated flywheel guidance: {json.dumps(flywheel, ensure_ascii=False)}; "
            "Use flywheel guidance only to choose among facts already present; never invent evidence from it. "
            f"account segment: {segment}. For luxury_group accounts, emphasize portfolio adjacency, selective distribution or boutique formats, VIP/private-client activation, and operating governance. "
            f"Use this exact subject unless it conflicts with a verified fact: {_email_subject(copy_contact, str(copy_contact.get('company_name') or 'your business'), recipient_mandate, segment)}. "
            f"experiment instruction: {str((experiment or {}).get('instruction') or 'none')}; "
            "The experiment instruction may change only the stated experiment variable and must not weaken factual accuracy. "
            f"sender: {_sender_signature_name(user, self.config.sender.get('name', ''))}."
        )
        text = gateway.complete(
            operation="personalized_email_draft",
            contact=contact,
            messages=[
                {"role": "system", "content": "You only output strict JSON for a B2B sales email draft."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=420,
            temperature=0.35,
            validate=_validated_draft_text,
        )
        if not text:
            return fallback
        try:
            if text.startswith("```"):
                text = text.strip("`")
                if text.lower().startswith("json"):
                    text = text[4:].strip()
            draft = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return fallback
        subject = str(draft.get("subject") or fallback["subject"])[:160]
        body = str(draft.get("body") or fallback["body"])[:3000]
        if segment in {"automotive_group", "luxury_group"}:
            subject = _email_subject(
                copy_contact,
                str(copy_contact.get("company_name") or "your business"),
                _recipient_mandate(copy_contact),
                segment,
            )
        if contains_internal_outreach_data(subject) or contains_internal_outreach_data(body):
            return fallback
        if review_email_copy(subject, body, contact=copy_contact)["status"] == "blocked":
            return fallback
        return {"subject": subject, "body": body}

    def _fallback_draft(
        self,
        contact: dict[str, Any],
        *,
        user: dict[str, Any] | None = None,
        research: dict[str, Any] | None = None,
    ) -> dict[str, str]:
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
        routes = _partnership_routes(contact, research)
        mandate = _recipient_mandate(contact)
        segment = _account_segment(contact, research)
        signature = _signature_profile(self.config, user)
        subject = _email_subject(contact, company, mandate, segment)
        opportunity = (
            "VERTU can complement the portfolio as a selective adjacent category spanning smartphones, watches, jewelry, leather goods and connected products."
            if segment == "luxury_group"
            else "VERTU can extend an existing relationship with affluent customers across smartphones, watches, jewelry, leather goods and connected products, supported by boutique-level service."
        )
        cta = (
            "Would a short discussion be useful to assess the market role, product mix and operating model before either side develops a formal partnership roadmap?"
            if segment == "luxury_group"
            else "Would it be useful if I sent a brief market-specific partnership deck covering the channel model, product mix and next steps?"
        )
        body = (
            f"Hi {first},\n\n"
            f"I’m {_sender_signature_name(user, self.config.sender.get('name', ''))} from VERTU’s international channel development team. "
            f"{match.rstrip('.')}.\n\n"
            f"{_APPROVED_BRAND_INTRO} {_market_intent(contact, segment)}\n\n"
            "VERTU in 2026 is no longer phones only. Our roadmap spans:\n"
            f"{_portfolio_list()}\n\n"
            f"{opportunity}\n\n"
            "These two practical routes may be worth assessing:\n"
            f"1. {routes[0]}\n"
            f"2. {routes[1]}\n\n"
            f"Given your responsibility for {mandate['responsibility']}, the useful question is {mandate['decision_question']}.\n\n"
            f"{cta}\n\n"
            f"{_sender_signature(user, self.config.sender.get('name', ''), signature)}\n\n"
            "Unsubscribe: {{unsubscribe_url}}"
        )
        return {"subject": subject, "body": body}


class OutreachService:
    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo

    def send_due(self, limit: int, *, user: dict[str, Any] | None = None) -> int:
        gateway = LLMGateway(self.config, self.repo)
        sent = 0
        attempted = 0
        for contact in self.repo.due_for_sending(limit, user=user):
            if attempted:
                sleep_between_sends(self.config)
            attempted += 1
            if self._send_contact(contact, gateway, user=user):
                sent += 1
        log("send.completed", sent=sent)
        return sent

    def send_contact(self, contact_id: int, *, user: dict[str, Any] | None = None) -> bool:
        gateway = LLMGateway(self.config, self.repo)
        contact = self.repo.due_contact_for_sending(contact_id, user=user)
        if not contact:
            return False
        sent = self._send_contact(contact, gateway, user=user)
        log("send.contact_completed", contact_id=contact_id, sent=sent)
        return sent

    def _send_contact(self, contact: dict[str, Any], gateway: LLMGateway, *, user: dict[str, Any] | None = None) -> bool:
        fresh = self.repo.due_contact_for_sending(int(contact["id"]), user=user)
        if not fresh:
            log("send.skipped_not_due", contact_id=contact.get("id"))
            return False
        contact = fresh
        sender_user = sender_identity_user(self.repo, contact, user)
        sender_user_id = int(sender_user["id"]) if sender_user else None
        actor_user_id = int(user["id"]) if user else None
        readiness = send_readiness(contact, strict_automation=True)
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
            "sender_signature": _sender_signature(sender_user, sender.get("name", ""), sender.get("signature")),
            "unsubscribe_url": unsubscribe_url(contact, base_url, tracking_secret),
            "account_context": _account_context(contact),
            "seed_reason": _source_context(contact).get("seed_reason", ""),
            "seed_category": _source_context(contact).get("seed_category", ""),
            "ai_opener": _ai_opener(gateway, contact) if step_cfg.get("ai_opener") else "",
            "partnership_route_1": _partnership_routes(contact)[0],
            "partnership_route_2": _partnership_routes(contact)[1],
            "recipient_responsibility": _recipient_mandate(contact)["responsibility"],
        }
        template = self.config.root_dir / step_cfg["body_template"]
        text, html_body = render_template(template, values)
        text = _normalize_sender_signature(text, sender_user, fallback_name=sender.get("name", ""), signature=sender.get("signature"), unsubscribe_value=values["unsubscribe_url"])
        html_body = build_html_body(text, product_images=self.config.product_images, signature=sender.get("signature"))
        validate_email_body(subject, text)
        quality_review = review_email_copy(subject, text, contact=contact)
        if quality_review["status"] == "blocked":
            reasons = [item["code"] for item in quality_review["blocking_issues"]]
            log("send.skipped_copy_policy", contact_id=contact.get("id"), reasons=reasons)
            return False
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
                attachments=_brand_attachments(self.config, sender.get("signature")),
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


_AUTOMOTIVE_SIGNALS = (
    "automotive",
    "dealer",
    "supercar",
    "luxury car",
    " cars",
    "porsche",
    "mercedes",
    "bmw",
    "bentley",
    "ferrari",
    "lamborghini",
)

_APPROVED_BRAND_INTRO = (
    "VERTU is a British luxury technology brand combining craftsmanship, technology and personalized service."
)
_APPROVED_2026_PORTFOLIO = (
    "Luxury smartphones",
    "Watches",
    "Jewelry",
    "Fine leather goods",
    "Luxury SUV",
    "Monthly new AIoT products",
)
_COUNTRY_ALIASES = {
    "india": "India",
    "turkey": "Türkiye",
    "türkiye": "Türkiye",
    "russia": "Russia",
    "kazakhstan": "Kazakhstan",
    "tajikistan": "Tajikistan",
    "kyrgyzstan": "Kyrgyzstan",
    "united arab emirates": "the UAE",
    "uae": "the UAE",
    "saudi arabia": "Saudi Arabia",
    "iran": "Iran",
    "iraq": "Iraq",
    "kuwait": "Kuwait",
    "bahrain": "Bahrain",
    "qatar": "Qatar",
    "oman": "Oman",
    "malaysia": "Malaysia",
    "singapore": "Singapore",
    "indonesia": "Indonesia",
    "thailand": "Thailand",
    "vietnam": "Vietnam",
    "philippines": "the Philippines",
    "united kingdom": "the UK",
    "uk": "the UK",
    "france": "France",
    "germany": "Germany",
    "italy": "Italy",
    "spain": "Spain",
    "united states": "the United States",
    "usa": "the United States",
}


def _partnership_routes(contact: dict[str, Any], research: dict[str, Any] | None = None) -> tuple[str, str]:
    segment = _account_segment(contact, research)
    if segment == "automotive_group":
        return (
            "a selective VERTU shop-in-shop or VIP display within the premium automotive network",
            "a regional distribution and client-activation model for existing high-value customers",
        )
    if segment == "luxury_group":
        return (
            "a curated VERTU category alongside the existing luxury portfolio",
            "a boutique, shop-in-shop or private-client event model built around VIP customers",
        )
    if segment == "hospitality_group":
        return (
            "a concierge, VIP gifting or private-client experience",
            "a selective retail or shop-in-shop format for affluent guests",
        )
    return (
        "selective local distribution with a controlled premium positioning",
        "a boutique or shop-in-shop model aligned with the existing customer base",
    )


def _market_intent(contact: dict[str, Any], segment: str) -> str:
    market = _market_name(contact)
    if market == "India":
        direction = "including automotive-related directions" if segment == "automotive_group" else "across broader luxury lifestyle categories"
        return f"VERTU is ready to return to India, and we are expanding beyond phones {direction}."
    if market:
        direction = "including automotive-related directions" if segment == "automotive_group" else "across broader luxury lifestyle categories"
        return f"VERTU is ready to expand in {market}, moving beyond phones {direction}."
    return "VERTU is expanding beyond phones across a broader luxury lifestyle portfolio."


def _portfolio_list() -> str:
    return "\n".join(f"- {item}" for item in _APPROVED_2026_PORTFOLIO)


def _market_name(contact: dict[str, Any]) -> str:
    context = _source_context(contact)
    direct = contact.get("country") or context.get("country")
    if direct:
        text = str(direct).strip()
        lowered = text.lower()
        return next((name for alias, name in _COUNTRY_ALIASES.items() if alias == lowered), text[:80])
    evidence = " ".join(
        str(value or "").lower()
        for value in (contact.get("location"), context.get("seed_location"), context.get("seed_reason"))
    )
    for alias, name in sorted(_COUNTRY_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", evidence):
            return name
    return ""


def _account_segment(contact: dict[str, Any], research: dict[str, Any] | None = None) -> str:
    context = _source_context(contact)
    company_segment = _segment_from_text(str(contact.get("company_name") or "").lower())
    if company_segment != "general":
        return company_segment
    primary_text = " ".join(
        str(value or "").lower()
        for value in (
            "" if contact.get("industry") == "premium retail and distribution" else contact.get("industry"),
            context.get("seed_category"),
            context.get("seed_reason"),
            contact.get("company_name"),
            contact.get("job_title"),
        )
    )
    segment = _segment_from_text(primary_text)
    if segment != "general" or not research:
        return segment
    return _segment_from_text(json.dumps(research, ensure_ascii=False, default=str).lower())


def _segment_from_text(text: str) -> str:
    signals = {
        "automotive_group": _AUTOMOTIVE_SIGNALS,
        "hospitality_group": ("hotel", "hospitality", "resort", "concierge"),
        "luxury_group": ("luxury", "watch", "jewelry", "jewellery", "diamond", "fashion", "boutique", "department store", "premium retail"),
    }
    scores = {segment: sum(token in text for token in tokens) for segment, tokens in signals.items()}
    best = max(scores.values())
    winners = [segment for segment, score in scores.items() if score == best]
    return winners[0] if best and len(winners) == 1 else "general"


def _email_subject(contact: dict[str, Any], company: str, mandate: dict[str, str], segment: str) -> str:
    if segment == "automotive_group":
        return f"VERTU × {company} — Luxury Tech + Automotive Synergy"
    if segment == "luxury_group" and mandate["subject_angle"] == "strategic growth paths":
        market = _market_name(contact)
        market_prefix = f"{market} " if market else ""
        return f"Strategic Vision: VERTU × {company} — {market_prefix}Partnership Roadmap"
    return f"VERTU × {company} — {mandate['subject_angle']}"


def _recipient_mandate(contact: dict[str, Any]) -> dict[str, str]:
    title = str(contact.get("job_title") or "").lower()
    if any(token in title for token in ("owner", "founder", "ceo", "president", "chairman", "general manager", "general director", "managing director")):
        return {
            "responsibility": "growth, portfolio strategy and partner economics",
            "decision_question": "whether VERTU creates a credible adjacent luxury category rather than operational distraction",
            "subject_angle": "strategic growth paths",
        }
    if any(token in title for token in ("buyer", "buying", "procurement", "merchand", "category")):
        return {
            "responsibility": "assortment, customer fit and commercial performance",
            "decision_question": "which product mix and trial format could complement the existing portfolio",
            "subject_angle": "portfolio fit",
        }
    if any(token in title for token in ("retail", "store", "franchise", "operations", "channel")):
        return {
            "responsibility": "store format, service standards and rollout execution",
            "decision_question": "which boutique or shop-in-shop model can be operated consistently in the local network",
            "subject_angle": "retail format",
        }
    if any(token in title for token in ("marketing", "brand", "communications", "crm", "clienteling")):
        return {
            "responsibility": "brand relevance, VIP engagement and customer activation",
            "decision_question": "how VERTU can create a credible private-client story and activation plan",
            "subject_angle": "VIP client activation",
        }
    if any(token in title for token in ("design", "product", "technology", "technical", "engineering")):
        return {
            "responsibility": "product experience, differentiation and technical delivery",
            "decision_question": "where product experience and luxury craftsmanship can create a meaningful collaboration",
            "subject_angle": "product collaboration",
        }
    return {
        "responsibility": "commercial development and local partner selection",
        "decision_question": "which cooperation model best fits the customer base and local operation",
        "subject_angle": "partnership options",
    }


def _fallback_opening(contact: dict[str, Any]) -> str:
    company = contact.get("company_name") or "your company"
    context = _source_context(contact)
    signal = context.get("public_signal") or context.get("seed_reason")
    if signal:
        return f"I noticed this public signal about {company}: {signal}"
    if context.get("seed_category"):
        return f"I noticed {company} is relevant to {context['seed_category']} and thought this might be worth a quick conversation."
    return f"I noticed your work as {contact.get('job_title') or 'a leader'} at {company} and thought this might be relevant."


def _ai_opener(gateway: LLMGateway, contact: dict[str, Any]) -> str:
    company = contact.get("company_name") or "your company"
    context = _source_context(contact)
    fallback = _fallback_opening(contact)
    prompt = (
        "Write exactly one concise, specific, non-hype cold email opening sentence. "
        "You are the sender writing to the recipient; never claim to work at or lead the recipient company. "
        "Use only the provided fields. Do not use placeholders, brackets, invented competitors, invented tools, or invented facts. "
        "Do not mention launches, funding, hiring, growth, tools, competitors, case studies, or recent events. "
        "If an account research note is provided, use it only as a plain observation and do not add claims beyond it. "
        f"Recipient role: {contact.get('job_title')}. Recipient company: {company}. "
        f"Industry/category: {context.get('seed_category') or contact.get('industry')}. "
        f"Account research note: {context.get('seed_reason') or ''}."
    )
    text = gateway.complete(
        operation="sequence_opener",
        contact=contact,
        messages=[
            {"role": "system", "content": "You write concise B2B cold email opening sentences. Do not invent facts."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=60,
        temperature=0.4,
        validate=_validated_opener_text,
    )
    candidate = " ".join(str(text or "").split())[:500]
    return candidate if candidate and "{" not in candidate and "[" not in candidate else fallback


def _reply_to_email(user: dict[str, Any] | None) -> str | None:
    value = str((user or {}).get("reply_to_email") or "").strip()
    if "@" not in value or " " in value:
        return None
    return value


def _validated_draft_text(text: str) -> str | None:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = value.strip("`")
        if value.lower().startswith("json"):
            value = value[4:].strip()
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        return None
    subject = str(parsed.get("subject") or "").strip()[:160]
    body = str(parsed.get("body") or "").strip()[:3000]
    if not subject or not body or contains_internal_outreach_data(subject) or contains_internal_outreach_data(body):
        return None
    if review_email_copy(subject, body)["status"] == "blocked":
        return None
    return json.dumps({"subject": subject, "body": body}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validated_opener_text(text: str) -> str | None:
    value = " ".join(str(text or "").split())[:500]
    if not value or "{" in value or "[" in value or contains_internal_outreach_data(value):
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


def _signature_profile(config: AppConfig, user: dict[str, Any] | None) -> dict[str, Any]:
    mailbox = sales_mailbox(config, user) or {}
    return dict(mailbox.get("signature") or {})


def _sender_signature(user: dict[str, Any] | None, fallback_name: str = "", signature: dict[str, Any] | None = None) -> str:
    name = _sender_signature_name(user, fallback_name)
    profile = signature or {}
    if profile:
        lines = [
            "Best regards,",
            str(profile.get("name") or name).strip(),
            str(profile.get("title") or "").strip(),
            str(profile.get("phone") or "").strip(),
            str(profile.get("address") or "").strip(),
        ]
        return "\n".join(line for line in lines if line)
    normalized = name.lower()
    if normalized in {"april", "jingjing yang", "jingjing yang (april)"}:
        return (
            "Best regards,\nApril Yang\n"
            "Head of CIS & South Asia | Vertu International Corporation Limited\n"
            "Phone: +008619003165328 | Room 505, 5th Floor, Beverley Commercial Centre,\n"
            "87-105 Chatham Road South, Tsim Sha Tsui, Kowloon. Hong Kong"
        )
    signature_name = name if name.lower().endswith(" you") else f"{name} You"
    return f"Best regards,\n{signature_name}\nBD Manager Of Media East Region | VERTU"


_SIGNOFF_RE = re.compile(r"\n+(?:Best|Best regards|Regards),\s*\n(?:[^\n]*\n?){0,4}\s*$", re.IGNORECASE)


def _normalize_sender_signature(
    text: str,
    user: dict[str, Any] | None,
    *,
    fallback_name: str = "",
    signature: dict[str, Any] | None = None,
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
    parts = [part for part in [body, _sender_signature(user, fallback_name, signature), unsubscribe] if part]
    return "\n\n".join(parts)


def _brand_attachments(config: AppConfig, signature: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    outreach = config.raw.get("outreach", {})
    configured = str(outreach.get("brand_pdf_path") or "").strip()
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            path = config.root_dir / path
        if path.suffix.lower() != ".pdf" or not path.is_file():
            raise RuntimeError(f"Configured VERTU brand PDF is missing or invalid: {path}")
        content = path.read_bytes()
        if not content.startswith(b"%PDF") or len(content) > 20 * 1024 * 1024:
            raise RuntimeError("Configured VERTU brand PDF must be a valid PDF no larger than 20 MB")
        filename = str(outreach.get("brand_pdf_filename") or "VERTU Brand Introduction.pdf").strip()
        attachments.append({"filename": filename, "content": content, "content_type": "application/pdf"})

    profile = signature or {}
    logo_path = Path(str(profile.get("logo_path") or "").strip())
    if str(logo_path) not in {"", "."}:
        if not logo_path.is_absolute():
            logo_path = config.root_dir / logo_path
        if logo_path.suffix.lower() not in {".png", ".jpg", ".jpeg"} or not logo_path.is_file():
            raise RuntimeError(f"Configured signature logo is missing or invalid: {logo_path}")
        logo_content = logo_path.read_bytes()
        if not logo_content or len(logo_content) > 1024 * 1024:
            raise RuntimeError("Configured signature logo must be no larger than 1 MB")
        attachments.append(
            {
                "filename": logo_path.name,
                "content": logo_content,
                "content_type": "image/png" if logo_path.suffix.lower() == ".png" else "image/jpeg",
                "disposition": "inline",
                "content_id": str(profile.get("logo_cid") or "vertu-signature-logo").strip("<>"),
            }
        )
    return attachments


__all__ = ["OutreachService", "PersonalizedEmailService"]
