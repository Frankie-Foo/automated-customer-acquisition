from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from .clients import is_full_email
from .config import AppConfig
from .http import HttpClient
from .logging_utils import log


class ContactOutAdapter(Protocol):
    def enrich(self, account: dict[str, Any], contact: dict[str, Any]) -> dict[str, Any]: ...


class ContactOutBlocked(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ContactOutRateLimited(RuntimeError):
    def __init__(self, retry_after_seconds: int = 3600):
        self.retry_after_seconds = max(60, int(retry_after_seconds))
        super().__init__("rate_limited")


class ContactOutBridgeAdapter:
    """Calls an internal bridge that owns the authorized browser session."""

    def __init__(self, config: AppConfig, *, http: HttpClient | None = None):
        self.config = config
        self.http = http or HttpClient()

    def enrich(self, account: dict[str, Any], contact: dict[str, Any]) -> dict[str, Any]:
        cfg = self.config.raw.get("contactout", {})
        url = str(cfg.get("bridge_url") or "").rstrip("/")
        key = str(self.config.apis.get("contactout_bridge_key") or "")
        if not url or not key:
            raise ContactOutBlocked("adapter_unconfigured")
        data = self.http.request(
            "POST",
            f"{url}/enrich",
            headers={"Authorization": f"Bearer {key}"},
            json_body={
                "credential_ref": account["credential_ref"],
                "contact": {
                    "id": contact.get("id"),
                    "first_name": contact.get("first_name"),
                    "last_name": contact.get("last_name"),
                    "company_name": contact.get("company_name"),
                    "company_domain": contact.get("company_domain"),
                    "job_title": contact.get("job_title"),
                    "linkedin_url": contact.get("linkedin_url"),
                },
            },
        )
        status = str(data.get("status") or "").lower()
        if status in {"challenge_required", "reauth_required"}:
            raise ContactOutBlocked(status)
        if status == "rate_limited":
            raise ContactOutRateLimited(int(data.get("retry_after_seconds") or 3600))
        return data


@dataclass(frozen=True)
class ContactOutRun:
    job_id: int
    status: str
    review_required: bool = False
    error_code: str | None = None


class ContactOutQueueService:
    def __init__(self, config: AppConfig, repo: Any, *, adapter: ContactOutAdapter | None = None):
        self.config = config
        self.repo = repo
        self.adapter = adapter or ContactOutBridgeAdapter(config)

    def enqueue(self, contact_id: int, *, owner_user_id: int, account_id: int) -> dict[str, Any]:
        contact = self.repo.get_contact(contact_id)
        if not contact:
            raise ValueError("contact_not_found")
        linkedin_url = _normalize_linkedin(contact.get("linkedin_url"))
        if not linkedin_url:
            raise ValueError("linkedin_url_required")
        account = self.repo.get_contactout_account(account_id, owner_user_id=owner_user_id)
        if not account or account.get("status") != "active":
            raise ValueError("contactout_account_unavailable")
        refresh_window = datetime.now(UTC).strftime("%Y-%m")
        input_hash = hashlib.sha256(linkedin_url.encode("utf-8")).hexdigest()
        idempotency_key = hashlib.sha256(
            f"contactout:v1|{contact_id}|{input_hash}|person_enrich|{refresh_window}".encode("utf-8")
        ).hexdigest()
        return self.repo.enqueue_contactout_job(
            contact_id=contact_id,
            owner_user_id=owner_user_id,
            account_id=account_id,
            operation="person_enrich",
            input_hash=input_hash,
            idempotency_key=idempotency_key,
        )

    def run_next(self) -> ContactOutRun | None:
        self.repo.block_expired_contactout_jobs()
        job = self.repo.claim_contactout_job()
        if not job:
            return None
        account = self.repo.get_contactout_account(int(job["account_id"]), owner_user_id=job.get("owner_user_id"))
        contact = self.repo.get_contact(int(job["contact_id"]))
        if not account or not contact:
            self.repo.fail_contactout_job(int(job["id"]), "missing_account_or_contact", consumed=False)
            return ContactOutRun(int(job["id"]), "failed", error_code="missing_account_or_contact")
        global_limit = max(0, int(self.config.raw.get("contactout", {}).get("global_daily_limit") or 0))
        if not self.repo.reserve_contactout_job_quota(int(job["id"]), global_limit=global_limit):
            self.repo.retry_contactout_job(int(job["id"]), "daily_quota_exhausted", retry_after_seconds=_seconds_until_tomorrow())
            return ContactOutRun(int(job["id"]), "retry_wait", error_code="daily_quota_exhausted")
        try:
            raw = self.adapter.enrich(account, contact)
            normalized = _normalize_result(raw)
            if normalized["match_status"] == "no_match":
                self.repo.complete_contactout_job(int(job["id"]), status="no_match", result=normalized)
                return ContactOutRun(int(job["id"]), "no_match")
            self.repo.complete_contactout_job(int(job["id"]), status="succeeded", result=normalized)
            return ContactOutRun(int(job["id"]), "succeeded", normalized["review_required"])
        except ContactOutRateLimited as exc:
            self.repo.retry_contactout_job(int(job["id"]), "rate_limited", retry_after_seconds=exc.retry_after_seconds)
            return ContactOutRun(int(job["id"]), "retry_wait", error_code="rate_limited")
        except ContactOutBlocked as exc:
            self.repo.block_contactout_job(int(job["id"]), exc.code)
            return ContactOutRun(int(job["id"]), "blocked", error_code=exc.code)
        except Exception as exc:
            log("contactout.failed", job_id=job["id"], error_type=type(exc).__name__)
            self.repo.fail_contactout_job(int(job["id"]), "provider_error", consumed=True)
            return ContactOutRun(int(job["id"]), "failed", error_code="provider_error")


def _normalize_result(data: dict[str, Any]) -> dict[str, Any]:
    match_status = str(data.get("match_status") or data.get("status") or "matched").lower()
    if match_status in {"no_match", "not_found"}:
        return {
            "match_status": "no_match",
            "match_confidence": 0,
            "review_required": False,
            "profile_url": data.get("profile_url"),
            "email_candidates": [],
            "phone_candidates": [],
        }
    confidence = max(0, min(100, int(data.get("match_confidence") or 0)))
    exact = match_status in {"matched", "exact"} and confidence >= 80
    emails = []
    for item in data.get("emails") or []:
        item = {"email": item} if isinstance(item, str) else dict(item)
        email = str(item.get("email") or "").strip().lower()
        if not is_full_email(email):
            continue
        emails.append(
            {
                "email": email,
                "source": "contactout",
                "status": str(item.get("status") or "unverified").lower(),
                "confidence": max(0, min(100, int(item.get("confidence") or confidence))),
                "category": str(item.get("category") or "personal"),
                "discovered_at": datetime.now(UTC).isoformat(),
            }
        )
    phones = []
    for item in data.get("phones") or []:
        item = {"phone": item} if isinstance(item, str) else dict(item)
        phone = str(item.get("phone") or "").strip()
        if len(re.sub(r"\D", "", phone)) < 7:
            continue
        phones.append(
            {
                "phone": phone,
                "source": "contactout",
                "status": str(item.get("status") or "unverified"),
                "scope": str(item.get("scope") or "person"),
                "channel": str(item.get("channel") or "mobile"),
                "confidence": max(0, min(100, int(item.get("confidence") or confidence))),
            }
        )
    return {
        "match_status": "exact" if exact else "review",
        "match_confidence": confidence,
        "review_required": not exact,
        "profile_url": data.get("profile_url"),
        "email_candidates": _dedupe(emails, "email"),
        "phone_candidates": _dedupe(phones, "phone"),
    }


def _normalize_linkedin(value: Any) -> str:
    url = str(value or "").strip().split("?", 1)[0].rstrip("/")
    return url.lower() if "linkedin.com/in/" in url.lower() else ""


def _dedupe(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for item in items:
        value = str(item.get(key) or "").lower()
        if value and int(item.get("confidence") or 0) >= int(best.get(value, {}).get("confidence") or -1):
            best[value] = item
    return list(best.values())


def _seconds_until_tomorrow() -> int:
    now = datetime.now(UTC)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    return max(60, int((tomorrow - now).total_seconds()))


__all__ = [
    "ContactOutAdapter",
    "ContactOutBlocked",
    "ContactOutBridgeAdapter",
    "ContactOutQueueService",
    "ContactOutRateLimited",
    "ContactOutRun",
]
