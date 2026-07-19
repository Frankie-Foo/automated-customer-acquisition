from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable

from ..clients import _domain_from_website
from ..customer_intelligence import build_customer_profile
from ..db import Repository
from ..regional_sourcing import detect_regional_profile


EMAIL_RE = re.compile(r"^[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.IGNORECASE)


class LeadWorkflowService:
    """Connect raw lead intake to the existing contact and sales-workflow records."""

    def __init__(self, repo: Repository):
        self.repo = repo

    def ingest_contacts(
        self,
        contacts: Iterable[dict[str, Any]],
        *,
        user: dict[str, Any],
        source_type: str,
        source_ref: str | None = None,
        campaign_id: int | None = None,
    ) -> dict[str, Any]:
        rows = list(contacts)
        result: dict[str, Any] = {
            "parsed": len(rows),
            "inserted": 0,
            "duplicates": 0,
            "linked": 0,
            "tasks_created": 0,
            "contact_ids": [],
        }
        owner_user_id = int(user["id"])
        owner_name = str(user.get("display_name") or user.get("username") or f"User {owner_user_id}")
        if campaign_id is None and rows and source_type != "manual_entry":
            campaign = self.repo.create_campaign(
                name=(source_ref or source_type)[:200],
                channel=source_type,
                owner_user_id=owner_user_id,
                metadata={"source_ref": source_ref},
            )
            campaign_id = int(campaign["id"])
        result["campaign_id"] = campaign_id

        for row_number, raw_contact in enumerate(rows, start=1):
            prepared = prepare_lead(raw_contact)
            if hasattr(self.repo, "blacklist_match"):
                blocked = self.repo.blacklist_match(
                    email=prepared.get("email"),
                    domain=prepared.get("company_domain"),
                )
                if blocked:
                    prepared["status"] = "unsubscribed"
                    prepared["disposition"] = "abandoned"
                    prepared["source_context"] = {
                        **prepared.get("source_context", {}),
                        "blocked_reason": blocked.get("reason") or "blacklist",
                    }
            prepared.setdefault("owner", owner_name)
            prepared["owner_user_id"] = owner_user_id
            prepared["pool_type"] = "private"
            prepared["assignment_source"] = prepared.get("assignment_source") or source_type

            duplicate = self.repo.find_contact_match(prepared)
            if duplicate:
                prepared["linkedin_url"] = duplicate["linkedin_url"]
                result["duplicates"] += 1

            inserted, _ = self.repo.upsert_contacts(
                [prepared],
                owner_user_id=owner_user_id,
                pool_type="private",
            )
            result["inserted"] += inserted
            contact = self.repo.get_contact_by_linkedin_url(prepared["linkedin_url"])
            if not contact:
                continue

            contact_id = int(contact["id"])
            if contact_id not in result["contact_ids"]:
                result["contact_ids"].append(contact_id)
            lead = self.repo.upsert_lead_record(
                external_id=lead_external_id(source_type, source_ref, row_number, prepared),
                source_type=source_type,
                source_ref=source_ref,
                source_row=row_number,
                campaign_id=campaign_id,
                contact_id=contact_id,
                owner_user_id=owner_user_id,
                raw_data={**raw_contact, "original_linkedin_url": raw_contact.get("linkedin_url")},
                normalized_email=prepared.get("email"),
                normalized_phone=normalize_phone(prepared.get("phone")),
                normalized_whatsapp=normalize_phone(_whatsapp_value(prepared)),
                company_domain=prepared.get("company_domain"),
                country=prepared.get("country"),
                region=prepared.get("region"),
                language=prepared.get("language"),
                dedupe_key=dedupe_key(prepared),
                status="duplicate" if duplicate else "promoted",
                quality_score=prepared.get("lead_score"),
            )
            result["linked"] += 1
            if self.ensure_next_task(contact_id, owner_user_id=owner_user_id, lead_id=int(lead["id"])):
                result["tasks_created"] += 1
            self.repo.refresh_customer_profile_snapshot(contact_id)
        if campaign_id:
            self.repo.refresh_campaign_metrics(campaign_id)
        return result

    def register_contacts(
        self,
        contact_ids: Iterable[int],
        *,
        user: dict[str, Any] | None,
        source_type: str,
        source_ref: str | None = None,
        campaign_id: int | None = None,
    ) -> dict[str, int]:
        result = {"linked": 0, "tasks_created": 0}
        contact_ids = list(dict.fromkeys(int(value) for value in contact_ids))
        if not contact_ids:
            return result
        required_methods = ("get_contact", "upsert_lead_record", "ensure_followup_task")
        if any(not hasattr(self.repo, method) for method in required_methods):
            return result
        if campaign_id is None and hasattr(self.repo, "create_campaign"):
            campaign = self.repo.create_campaign(
                name=(source_ref or source_type)[:200],
                channel=source_type,
                owner_user_id=int(user["id"]) if user else None,
                metadata={"source_ref": source_ref},
            )
            campaign_id = int(campaign["id"])
        for row_number, contact_id in enumerate(contact_ids, start=1):
            contact = self.repo.get_contact(contact_id)
            if not contact:
                continue
            owner_user_id = contact.get("owner_user_id") or (user or {}).get("id")
            lead = self.repo.upsert_lead_record(
                external_id=lead_external_id(source_type, source_ref, row_number, contact),
                source_type=source_type,
                source_ref=source_ref,
                source_row=row_number,
                campaign_id=campaign_id,
                contact_id=contact_id,
                owner_user_id=int(owner_user_id) if owner_user_id else None,
                raw_data={"contact_snapshot": _lead_snapshot(contact)},
                normalized_email=normalize_email(contact.get("email")),
                normalized_phone=normalize_phone(contact.get("phone")),
                normalized_whatsapp=normalize_phone(_whatsapp_value(contact)),
                company_domain=normalize_domain(contact.get("company_domain")),
                country=_regional_fields(contact)[0],
                region=_regional_fields(contact)[1],
                language=_regional_fields(contact)[2],
                dedupe_key=dedupe_key(contact),
                status="promoted",
                quality_score=_quality_score(contact),
            )
            result["linked"] += 1
            if self.ensure_next_task(contact_id, owner_user_id=owner_user_id, lead_id=int(lead["id"])):
                result["tasks_created"] += 1
        if campaign_id and hasattr(self.repo, "refresh_campaign_metrics"):
            self.repo.refresh_campaign_metrics(campaign_id)
        return result

    def ensure_next_task(
        self,
        contact_id: int,
        *,
        owner_user_id: int | None = None,
        lead_id: int | None = None,
    ) -> dict[str, Any] | None:
        contact = self.repo.get_contact(contact_id)
        if not contact:
            return None
        owner_user_id = int(owner_user_id or contact.get("owner_user_id") or 0) or None
        task = next_task_for_contact(contact)
        if not task:
            return None
        return self.repo.ensure_followup_task(
            contact_id=contact_id,
            lead_id=lead_id,
            assigned_user_id=owner_user_id,
            created_by_user_id=owner_user_id,
            **task,
        )

    def refresh_tasks(self, *, user: dict[str, Any] | None = None, limit: int = 500) -> int:
        created = 0
        for contact in self.repo.list_contacts(user=user, limit=max(1, min(int(limit), 1000))):
            if self.ensure_next_task(
                int(contact["id"]),
                owner_user_id=contact.get("owner_user_id"),
            ):
                created += 1
        return created


def prepare_lead(contact: dict[str, Any]) -> dict[str, Any]:
    prepared = {key: value for key, value in contact.items() if value not in (None, "")}
    has_verified_identity_url = bool(str(prepared.get("linkedin_url") or "").strip())
    email = normalize_email(prepared.get("email"))
    if email:
        prepared["email"] = email
        prepared["email_status"] = prepared.get("email_status") or "valid"
        prepared["status"] = "enriched"
    else:
        prepared.pop("email", None)
        prepared.setdefault("status", "new")
    if prepared.get("phone"):
        prepared["phone"] = normalize_phone(prepared["phone"]) or str(prepared["phone"]).strip()
    prepared["company_domain"] = normalize_domain(prepared.get("company_domain") or prepared.get("website"))
    country, region, language = _regional_fields(prepared)
    prepared["country"] = country
    prepared["region"] = region
    prepared["language"] = language
    source_context = prepared.get("source_context") if isinstance(prepared.get("source_context"), dict) else {}
    profile = build_customer_profile(prepared)
    prepared["lead_score"] = int(profile["icp_fit_score"])
    prepared["source_context"] = {
        **source_context,
        "country": country,
        "region": region,
        "language": language,
        "score_breakdown": profile["fit_score_breakdown"],
        "score_reason": profile["summary"],
        "recommended_channel": recommended_channel(prepared),
        "next_action": profile["next_action"],
    }
    if not str(prepared.get("linkedin_url") or "").strip():
        identity = dedupe_key(prepared).removeprefix("identity:")
        prepared["linkedin_url"] = f"urn:lead:{identity}"
    prepared.setdefault("identity_confidence", prepared["lead_score"])
    prepared.setdefault("identity_status", "confirmed" if has_verified_identity_url and prepared["lead_score"] >= 70 else "review")
    return prepared


def next_task_for_contact(contact: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any] | None:
    now = now or datetime.now(UTC)
    status = str(contact.get("status") or "new")
    name = _display_name(contact)
    if status in {"unsubscribed"} or str(contact.get("disposition") or "") in {"abandoned", "lost"}:
        return None
    if status == "replied" or int(contact.get("replied_count") or 0) > 0:
        return _task("reply", "urgent", f"回复并推进 {name}", "客户已回复，确认需求、决策人和下一次沟通时间。", now, "reply_received")
    if status == "bounced" or int(contact.get("bounced_count") or 0) > 0:
        return _task("fix_contact", "high", f"处理 {name} 的退信", "核对邮箱，无法确认时加入黑名单并改用电话或社媒。", now, "email_bounced")
    if int(contact.get("opened_count") or 0) > 0 and status not in {"replied", "bounced", "unsubscribed"}:
        return _task("followup", "high", f"跟进已打开的客户 {name}", "客户已打开但未回复，补充一个新的业务价值点。", now, "opened_no_reply")
    if status == "sent_1":
        return _task("followup", "normal", f"准备第 2 次触达：{name}", "首封邮件未回复，换一个更具体的业务角度。", now + timedelta(days=3), "sent_1_no_reply")
    if status == "sent_2":
        return _task("followup", "normal", f"准备第 3 次触达：{name}", "第二次触达仍未回复，只补充一个新价值点。", now + timedelta(days=4), "sent_2_no_reply")
    if status == "sent_3":
        return _task("waiting_pool", "low", f"关闭触达序列：{name}", "第三次触达后仍未回复，转入等待池；已打开客户可保留人工跟进。", now + timedelta(days=7), "sent_3_close")
    if contact.get("email_status") == "valid" and contact.get("email"):
        draft = contact.get("draft_status")
        if draft == "approved":
            return _task("send", "high", f"发送已审核邮件：{name}", "邮件已审核，可在配额和工作时间内发送。", now, "approved_draft_ready")
        if draft == "draft":
            return _task("review_draft", "normal", f"审核首封邮件：{name}", "检查客户事实、主题、正文和收件邮箱后确认发送。", now + timedelta(hours=24), "first_touch_review")
        return _task("generate_draft", "normal", f"生成个性化邮件：{name}", "根据客户资料和公开事实生成首封邮件，生成后由销售审核。", now + timedelta(hours=24), "first_touch_draft")
    return _task("enrich_contact", "normal", f"补齐联系方式：{name}", "优先核验负责人身份和个人工作邮箱；公司通用邮箱仅作为候选。", now + timedelta(hours=24), "missing_valid_email")


def normalize_email(value: Any) -> str | None:
    email = str(value or "").strip().lower()
    if not email or "*" in email or not EMAIL_RE.fullmatch(email):
        return None
    return email


def normalize_phone(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    has_plus = raw.startswith("+")
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 7 or len(digits) > 15:
        return None
    return f"+{digits}" if has_plus else digits


def normalize_domain(value: Any) -> str | None:
    return _domain_from_website(str(value or "")) or None


def dedupe_key(contact: dict[str, Any]) -> str:
    email = normalize_email(contact.get("email"))
    if email:
        return f"email:{email}"
    phone = normalize_phone(contact.get("phone"))
    if phone:
        return f"phone:{phone}"
    linkedin_url = str(contact.get("linkedin_url") or "").strip().lower().rstrip("/")
    if linkedin_url and not linkedin_url.startswith("urn:csv:"):
        return f"linkedin:{linkedin_url}"
    identity = "|".join(
        str(contact.get(key) or "").strip().casefold()
        for key in ("first_name", "last_name", "company_domain", "company_name")
    )
    return f"identity:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}"


def lead_external_id(source_type: str, source_ref: str | None, row_number: int, contact: dict[str, Any]) -> str:
    seed = f"{source_type}|{source_ref or ''}|{row_number}|{dedupe_key(contact)}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def recommended_channel(contact: dict[str, Any]) -> str:
    if normalize_email(contact.get("email")):
        return "email"
    if _whatsapp_value(contact) or normalize_phone(contact.get("phone")):
        return "whatsapp_or_phone"
    if contact.get("linkedin_url"):
        return "linkedin"
    return "research_required"


def _regional_fields(contact: dict[str, Any]) -> tuple[str | None, str, str]:
    profile = detect_regional_profile(
        contact.get("location"),
        contact.get("country"),
        contact.get("region"),
        contact.get("industry"),
    )
    language = str(contact.get("language") or (profile.search_languages[0] if profile.search_languages else "en"))
    country = str(contact.get("country") or profile.country or "").strip() or None
    return country, profile.key, language


def _quality_score(contact: dict[str, Any]) -> int:
    return int(contact.get("lead_score") or build_customer_profile(contact)["icp_fit_score"])


def _whatsapp_value(contact: dict[str, Any]) -> Any:
    context = contact.get("source_context") if isinstance(contact.get("source_context"), dict) else {}
    return contact.get("whatsapp") or context.get("whatsapp")


def _display_name(contact: dict[str, Any]) -> str:
    person = " ".join(str(contact.get(key) or "").strip() for key in ("first_name", "last_name")).strip()
    return person or str(contact.get("company_name") or "客户")


def _lead_snapshot(contact: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "linkedin_url", "first_name", "last_name", "email", "phone", "job_title",
        "company_name", "company_domain", "industry", "location", "source", "source_context",
    )
    return {key: contact.get(key) for key in keys if contact.get(key) not in (None, "")}


def _task(
    task_type: str,
    priority: str,
    title: str,
    description: str,
    due_at: datetime,
    trigger_rule: str,
) -> dict[str, Any]:
    return {
        "task_type": task_type,
        "priority": priority,
        "title": title,
        "description": description,
        "due_at": due_at.isoformat(),
        "trigger_rule": trigger_rule,
        "metadata": {"generated_by": "lead_workflow"},
    }


__all__ = [
    "LeadWorkflowService",
    "dedupe_key",
    "lead_external_id",
    "next_task_for_contact",
    "normalize_domain",
    "normalize_email",
    "normalize_phone",
    "prepare_lead",
    "recommended_channel",
]
