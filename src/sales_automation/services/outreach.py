from __future__ import annotations

import html
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from ..clients import LLMClient, MailClient, is_full_email
from ..config import AppConfig
from ..db import Repository
from ..logging_utils import log
from ..rendering import open_pixel_url, render_string, render_template, unsubscribe_url
from ..sender_pool import SenderPoolManager


class PersonalizedEmailService:
    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo

    def draft(self, contact_id: int, *, mode: str = "ai", custom_subject: str | None = None, custom_body: str | None = None) -> dict[str, str]:
        contact = self.repo.get_contact(contact_id)
        if not contact:
            raise ValueError("Contact not found")
        if mode == "custom":
            return {
                "subject": custom_subject or f"Quick question about {contact.get('company_name') or 'your business'}",
                "body": custom_body or "",
            }
        return self._ai_draft(contact)

    def send(self, contact_id: int, *, subject: str, body: str, mode: str = "custom") -> dict[str, Any]:
        contact = self.repo.get_contact(contact_id)
        if not contact:
            raise ValueError("Contact not found")
        if not is_full_email(contact.get("email")):
            raise ValueError("Contact does not have a valid email")
        sender_pool = SenderPoolManager(self.config, self.repo)
        sender = sender_pool.pick_sender()
        api_key = self.config.apis.get(f"{sender.get('provider', 'resend')}_key", "")
        mailer = MailClient(sender.get("provider", "resend"), api_key, sender)
        base_url = self.config.raw.get("app", {}).get("public_base_url", "http://127.0.0.1:8765")
        step = int(contact.get("sequence_step") or 0) + 1
        values = {
            **contact,
            "sender_name": sender.get("name", ""),
            "unsubscribe_url": unsubscribe_url(contact, base_url),
        }
        text = render_string(body, values)
        if "{{unsubscribe_url}}" not in body:
            text = f"{text.rstrip()}\n\nUnsubscribe: {values['unsubscribe_url']}"
        html_body = "<br>".join(html.escape(line) for line in text.splitlines())
        html_body += f'<img src="{open_pixel_url(contact, step, base_url)}" width="1" height="1" alt="" />'
        message_id = mailer.send(
            contact["email"],
            subject,
            html_body,
            text,
            metadata={"contact_id": contact["id"], "sequence_step": step, "mode": mode},
        )
        metadata = {
            "dry_run": sender.get("dry_run", True),
            "mode": mode,
            "sender_id": sender.get("id"),
            "sender_email": sender.get("email"),
        }
        recorded = self.repo.record_manual_sent(contact["id"], step, subject, message_id, metadata)
        if recorded:
            sender_pool.record_send(sender)
        return {"sent": bool(recorded), "contact_id": contact_id, "step": step, "message_id": message_id}

    def _ai_draft(self, contact: dict[str, Any]) -> dict[str, str]:
        fallback = self._fallback_draft(contact)
        llm_cfg = self.config.raw.get("llm", {})
        provider = llm_cfg.get("provider", "deepseek")
        api_key = self.config.apis.get(f"{provider}_key", "") or self.config.apis.get("openai_key", "")
        if not api_key:
            return fallback
        insights = contact.get("profile_insights") if isinstance(contact.get("profile_insights"), dict) else {}
        activities = self.repo.list_lifecycle_activities(int(contact["id"]), limit=5)
        history = "\n".join(f"- {item.get('content')}" for item in activities if item.get("content"))
        prompt = (
            "你是海外B2B销售邮件助手。只根据客户资料生成一封简洁英文邮件，不编造事实。"
            "请只输出 JSON，不要 Markdown。字段：subject, body。body 使用纯文本，80-140词，语气自然。"
            "必须包含明确但轻量的下一步请求，不要夸大，不要承诺不存在的案例。"
            f"客户：{contact.get('first_name')} {contact.get('last_name')}；职位：{contact.get('job_title')}；"
            f"公司：{contact.get('company_name')}；行业：{contact.get('industry')}；地区：{contact.get('location')}；"
            f"生命周期：{contact.get('lifecycle_stage')}；画像：{insights}；历史记录：{history}；"
            f"发件人：{self.config.sender.get('name')}。"
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

    def _fallback_draft(self, contact: dict[str, Any]) -> dict[str, str]:
        company = contact.get("company_name") or "your business"
        first = contact.get("first_name") or "there"
        sender = self.config.sender.get("name", "")
        subject = f"Quick question about {company}"
        body = (
            f"Hi {first},\n\n"
            f"I noticed your work as {contact.get('job_title') or 'a leader'} at {company} and thought this might be relevant.\n\n"
            "Would it make sense to have a short conversation about overseas channel cooperation and the next practical step?\n\n"
            f"Best,\n{sender}\n\n"
            "Unsubscribe: {{unsubscribe_url}}"
        )
        return {"subject": subject, "body": body}


class OutreachService:
    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo

    def send_due(self, limit: int) -> int:
        ai = self._ai_client()
        sent = 0
        for contact in self.repo.due_for_sending(limit):
            if self._send_contact(contact, ai):
                sent += 1
        log("send.completed", sent=sent)
        return sent

    def send_contact(self, contact_id: int) -> bool:
        ai = self._ai_client()
        contact = self.repo.due_contact_for_sending(contact_id)
        if not contact:
            return False
        sent = self._send_contact(contact, ai)
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

    def _send_contact(self, contact: dict[str, Any], ai: LLMClient) -> bool:
        step_cfg = self._next_step_config(contact)
        if not step_cfg or not self._step_due(contact, step_cfg):
            return False
        sender_pool = SenderPoolManager(self.config, self.repo)
        sender = sender_pool.pick_sender()
        api_key = self.config.apis.get(f"{sender.get('provider', 'resend')}_key", "")
        mailer = MailClient(sender.get("provider", "resend"), api_key, sender)
        subject = render_string(step_cfg["subject"], contact)
        base_url = self.config.raw.get("app", {}).get("public_base_url", "http://127.0.0.1:8765")
        values = {
            **contact,
            "sender_name": sender.get("name", ""),
            "unsubscribe_url": unsubscribe_url(contact, base_url),
            "ai_opener": ai.opener(contact) if step_cfg.get("ai_opener") else "",
        }
        template = self.config.root_dir / step_cfg["body_template"]
        text, html_body = render_template(template, values)
        html_body += f'<img src="{open_pixel_url(contact, int(step_cfg["step"]), base_url)}" width="1" height="1" alt="" />'
        message_id = mailer.send(
            contact["email"],
            subject,
            html_body,
            text,
            metadata={"contact_id": contact["id"], "sequence_step": step_cfg["step"]},
        )
        metadata = {
            "dry_run": sender.get("dry_run", True),
            "sender_id": sender.get("id"),
            "sender_email": sender.get("email"),
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

__all__ = ["OutreachService", "PersonalizedEmailService"]
