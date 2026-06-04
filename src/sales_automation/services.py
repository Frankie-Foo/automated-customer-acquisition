from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .clients import HunterClient, LLMClient, MailClient, NinjaPearClient, ProspeoClient, ProxycurlClient, SlackClient, is_full_email
from .config import AppConfig
from .db import Repository
from .logging_utils import log
from .rendering import open_pixel_url, render_string, render_template, unsubscribe_url


class SourcingService:
    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo

    def source(self, criteria: dict[str, Any], limit: int) -> tuple[int, int]:
        provider = self.config.raw.get("sourcing", {}).get("provider", "prospeo")
        prospeo_key = self.config.apis.get("prospeo_key", "")
        ninjapear_key = self.config.apis.get("ninjapear_key", "")
        if provider == "prospeo" and prospeo_key:
            role = criteria.get("role") or criteria.get("title")
            if not role:
                raise RuntimeError("Prospeo sourcing requires role")
            try:
                contacts = ProspeoClient(prospeo_key).search_people(
                    company_website=criteria.get("company_website"),
                    role=role,
                    industry=criteria.get("industry"),
                    location=criteria.get("location"),
                    limit=limit,
                )
            except RuntimeError as exc:
                if "NO_RESULTS" in str(exc):
                    contacts = []
                else:
                    raise
        elif ninjapear_key:
            company_website = criteria.get("company_website") or criteria.get("company")
            role = criteria.get("role") or criteria.get("title")
            if not company_website or not role:
                raise RuntimeError("NinjaPear sourcing requires company_website and role")
            contacts = NinjaPearClient(ninjapear_key).search_employees(
                company_website=company_website,
                role=role,
                location=criteria.get("location"),
                limit=limit,
            )
        else:
            key = self.config.apis.get("proxycurl_key", "")
            if not key:
                raise RuntimeError("Missing apis.prospeo_key or apis.ninjapear_key")
            contacts = ProxycurlClient(key).search_people(criteria, limit)
        contacts = [c for c in contacts if c.get("linkedin_url")]
        inserted, skipped = self.repo.upsert_contacts(contacts)
        log("source.completed", inserted=inserted, skipped=skipped)
        return inserted, skipped


class EnrichmentService:
    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo

    def enrich(self, limit: int) -> tuple[int, int]:
        hunter_key = self.config.apis.get("hunter_key", "")
        ninjapear_key = self.config.apis.get("ninjapear_key", "")
        prospeo_key = self.config.apis.get("prospeo_key", "")
        proxycurl_key = self.config.apis.get("proxycurl_key", "")
        if not hunter_key and not ninjapear_key and not prospeo_key:
            raise RuntimeError("Missing apis.hunter_key, apis.prospeo_key, or apis.ninjapear_key")
        hunter = HunterClient(hunter_key) if hunter_key else None
        ninjapear = NinjaPearClient(ninjapear_key) if ninjapear_key else None
        prospeo = ProspeoClient(prospeo_key) if prospeo_key else None
        proxycurl = ProxycurlClient(proxycurl_key) if proxycurl_key else None
        ok = failed = 0
        for contact in self.repo.list_for_enrichment(limit):
            try:
                fields = self._enrich_one(contact, hunter, proxycurl, ninjapear, prospeo)
                note = None if fields.get("email_status") == "valid" else "No verified email found"
                self.repo.update_enrichment(contact["id"], fields, error=note)
                ok += 1
            except Exception as exc:
                self.repo.update_enrichment(contact["id"], {"email_status": "unknown"}, error=str(exc))
                failed += 1
                log("enrich.failed", contact_id=contact["id"], error=str(exc))
        log("enrich.completed", ok=ok, failed=failed)
        return ok, failed

    def _enrich_one(self, contact: dict[str, Any], hunter: HunterClient | None, proxycurl: ProxycurlClient | None, ninjapear: NinjaPearClient | None, prospeo: ProspeoClient | None) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        domain = contact.get("company_domain")
        if domain and is_full_email(contact.get("email")):
            fields["email"] = contact.get("email")
            fields["email_status"] = contact.get("email_status") or "valid"
        elif domain and ninjapear:
            found = ninjapear.find_work_email(domain=domain, first_name=contact.get("first_name"), last_name=contact.get("last_name"))
            email = found.get("work_email") or found.get("email")
            if email:
                fields["email"] = email
                fields["email_status"] = "valid"
        elif prospeo:
            try:
                found = prospeo.enrich_person(contact)
            except RuntimeError as exc:
                if "NO_MATCH" in str(exc):
                    return {"email_status": "unknown"}
                raise
            email_obj = found.get("email") or found.get("work_email")
            email = email_obj.get("email") if isinstance(email_obj, dict) else email_obj
            if is_full_email(email):
                fields["email"] = email
                fields["email_status"] = "valid"
        elif domain and hunter:
            found = hunter.find_email(domain, contact.get("first_name"), contact.get("last_name"))
            email = found.get("email")
            if email:
                verified = hunter.verify_email(email)
                fields["email"] = email
                fields["email_status"] = verified.get("status", "unknown")
        if proxycurl and domain:
            company = proxycurl.company_lookup(domain)
            fields["company_size"] = company.get("company_size") or company.get("employee_count")
            fields["company_funding"] = company.get("funding_data") or company.get("funding_stage")
            fields["industry"] = company.get("industry") or contact.get("industry")
        return fields or {"email_status": "unknown"}


class QueueService:
    def __init__(self, repo: Repository):
        self.repo = repo

    def queue(self, limit: int) -> int:
        count = self.repo.queue_contacts(limit)
        log("queue.completed", count=count)
        return count


class OutreachService:
    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo

    def send_due(self, limit: int) -> int:
        sender = self.config.sender
        daily_limit = int(sender.get("daily_limit", 100))
        remaining = max(0, daily_limit - self.repo.sent_today_count())
        if remaining == 0:
            log("send.skipped_daily_limit", daily_limit=daily_limit)
            return 0
        api_key = self.config.apis.get(f"{sender.get('provider', 'resend')}_key", "")
        mailer = MailClient(sender.get("provider", "resend"), api_key, sender)
        llm_cfg = self.config.raw.get("llm", {})
        provider = llm_cfg.get("provider", "deepseek")
        api_key = self.config.apis.get(f"{provider}_key", "") or self.config.apis.get("openai_key", "")
        ai = LLMClient(
            api_key,
            provider=provider,
            base_url=llm_cfg.get("base_url", "https://api.deepseek.com"),
            model=llm_cfg.get("model", "deepseek-chat"),
        )
        sent = 0
        for contact in self.repo.due_for_sending(min(limit, remaining)):
            step_cfg = self._next_step_config(contact)
            if not step_cfg or not self._step_due(contact, step_cfg):
                continue
            subject = render_string(step_cfg["subject"], contact)
            values = {
                **contact,
                "sender_name": sender.get("name", ""),
                "unsubscribe_url": unsubscribe_url(contact, self.config.raw.get("app", {}).get("public_base_url", "http://127.0.0.1:8765")),
                "ai_opener": ai.opener(contact) if step_cfg.get("ai_opener") else "",
            }
            template = self.config.root_dir / step_cfg["body_template"]
            text, html = render_template(template, values)
            base_url = self.config.raw.get("app", {}).get("public_base_url", "http://127.0.0.1:8765")
            html += f'<img src="{open_pixel_url(contact, int(step_cfg["step"]), base_url)}" width="1" height="1" alt="" />'
            message_id = mailer.send(contact["email"], subject, html, text, metadata={"contact_id": contact["id"], "sequence_step": step_cfg["step"]})
            if self.repo.record_sent(contact["id"], int(step_cfg["step"]), subject, message_id, {"dry_run": sender.get("dry_run", True)}):
                sent += 1
                log("send.sent", contact_id=contact["id"], step=step_cfg["step"], dry_run=sender.get("dry_run", True))
        log("send.completed", sent=sent)
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


class WebhookService:
    def __init__(self, repo: Repository, notifier: SlackClient | None = None):
        self.repo = repo
        self.notifier = notifier

    def process_payload(self, provider: str, payload: dict[str, Any]) -> str:
        contact_id = _extract_contact_id(payload)
        event_type = _extract_event_type(provider, payload)
        if not contact_id:
            raise ValueError("Webhook payload does not include contact_id metadata")
        self.repo.record_event(contact_id, event_type, payload)
        if event_type == "replied" and self.notifier:
            self.notifier.notify(f"Lead replied: contact #{contact_id}")
        log("webhook.processed", provider=provider, contact_id=contact_id, event_type=event_type)
        return event_type

    def process_file(self, provider: str, path: Path) -> str:
        return self.process_payload(provider, json.loads(path.read_text(encoding="utf-8")))


class SchedulerService:
    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo

    def run_once(self, enrich_limit: int, queue_limit: int, send_limit: int) -> None:
        with self.repo.db.connect() as conn:
            row = conn.execute("SELECT pg_try_advisory_lock(20260603) AS locked").fetchone()
            if not row["locked"]:
                log("scheduler.skipped_locked")
                return
            try:
                EnrichmentService(self.config, self.repo).enrich(enrich_limit)
                QueueService(self.repo).queue(queue_limit)
                OutreachService(self.config, self.repo).send_due(send_limit)
                log("scheduler.completed")
            finally:
                conn.execute("SELECT pg_advisory_unlock(20260603)")


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
    if "unsubscribe" in raw:
        return "unsubscribed"
    if "reply" in raw:
        return "replied"
    if "click" in raw:
        return "clicked"
    if "open" in raw:
        return "opened"
    return raw or "opened"
