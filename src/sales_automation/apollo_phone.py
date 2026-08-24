from __future__ import annotations

import hashlib
import hmac
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from .config import AppConfig
from .contactout_queue import contactout_bridge_configured
from .http import HttpClient
from .logging_utils import log


class ApolloPhoneAdapter(Protocol):
    def request_phone(self, contact: dict[str, Any], *, webhook_url: str) -> dict[str, Any]: ...


def apollo_phone_configured(config: AppConfig) -> bool:
    cfg = config.raw.get("apollo_phone", {})
    public_url = str(config.raw.get("app", {}).get("public_base_url") or "").rstrip("/")
    secret = str(config.raw.get("webhooks", {}).get("apollo_secret") or "")
    return bool(
        cfg.get("enabled")
        and str(config.apis.get("apollo_key") or "").strip()
        and public_url.startswith("https://")
        and len(secret) >= 24
    )


class ApolloApiAdapter:
    def __init__(self, config: AppConfig, *, http: HttpClient | None = None):
        self.config = config
        self.http = http or HttpClient(timeout=45)

    def request_phone(self, contact: dict[str, Any], *, webhook_url: str) -> dict[str, Any]:
        params: dict[str, Any] = {
            "reveal_personal_emails": "false",
            "reveal_phone_number": "true",
            "run_waterfall_email": "false",
            "run_waterfall_phone": "false",
            "webhook_url": webhook_url,
        }
        linkedin_url = str(contact.get("linkedin_url") or "").strip()
        if linkedin_url.startswith("http"):
            params["linkedin_url"] = linkedin_url
        else:
            if contact.get("first_name"):
                params["first_name"] = contact["first_name"]
            if contact.get("last_name"):
                params["last_name"] = contact["last_name"]
            if contact.get("company_domain"):
                params["domain"] = contact["company_domain"]
        if not params.get("linkedin_url") and not (params.get("first_name") and params.get("company_domain")):
            raise ValueError("apollo_identity_insufficient")
        url = "https://api.apollo.io/api/v1/people/match?" + urllib.parse.urlencode(params)
        return self.http.request(
            "POST",
            url,
            headers={
                "accept": "application/json",
                "Cache-Control": "no-cache",
                "x-api-key": str(self.config.apis.get("apollo_key") or ""),
            },
            json_body={},
            retries=1,
        )


@dataclass(frozen=True)
class ApolloPhoneRun:
    job_id: int
    status: str
    error_code: str | None = None


class ApolloPhoneQueueService:
    def __init__(self, config: AppConfig, repo: Any, *, adapter: ApolloPhoneAdapter | None = None):
        self.config = config
        self.repo = repo
        self.adapter = adapter or ApolloApiAdapter(config)

    def auto_enqueue(self, limit: int = 20) -> dict[str, Any]:
        limit = max(1, min(100, int(limit)))
        contactout_required = contactout_bridge_configured(self.config)
        candidates = self.repo.list_apollo_phone_candidates(
            limit=limit,
            require_contactout_terminal=contactout_required,
        )
        jobs = []
        for contact in candidates:
            input_hash = _identity_hash(contact)
            jobs.append(
                self.repo.enqueue_apollo_phone_job(
                    contact_id=int(contact["id"]),
                    owner_user_id=int(contact["owner_user_id"]),
                    input_hash=input_hash,
                    idempotency_key=hashlib.sha256(
                        f"apollo-phone:v1|{contact['id']}|{input_hash}|{datetime.now(_BUSINESS_TZ):%Y-%m}".encode("utf-8")
                    ).hexdigest(),
                    quota_units=self.max_credits_per_lookup,
                )
            )
        return {"candidates": len(candidates), "queued": len(jobs), "jobs": jobs}

    def dispatch_next(self) -> ApolloPhoneRun | None:
        self.repo.expire_apollo_phone_jobs()
        job = self.repo.claim_apollo_phone_job()
        if not job:
            return None
        global_limit = max(0, int(self.config.raw.get("apollo_phone", {}).get("global_daily_credit_limit") or 0))
        reservation = self.repo.reserve_apollo_phone_quota(
            int(job["id"]), str(job["lease_token"]), global_limit=global_limit
        )
        if reservation != "reserved":
            return ApolloPhoneRun(int(job["id"]), "blocked", reservation)
        contact = self.repo.get_contact(int(job["contact_id"]))
        if not contact:
            self.repo.fail_apollo_phone_dispatch(
                int(job["id"]), str(job["lease_token"]), "contact_not_found", charge_reserved=False
            )
            return ApolloPhoneRun(int(job["id"]), "failed", "contact_not_found")
        webhook_url = self.webhook_url(job)
        try:
            response = self.adapter.request_phone(contact, webhook_url=webhook_url)
        except ValueError as exc:
            self.repo.fail_apollo_phone_dispatch(
                int(job["id"]), str(job["lease_token"]), str(exc), charge_reserved=False
            )
            return ApolloPhoneRun(int(job["id"]), "failed", str(exc))
        except Exception as exc:
            log("apollo_phone.dispatch_unknown", job_id=job["id"], error_type=type(exc).__name__)
            self.repo.fail_apollo_phone_dispatch(
                int(job["id"]), str(job["lease_token"]), "dispatch_unknown", charge_reserved=True
            )
            return ApolloPhoneRun(int(job["id"]), "blocked", "dispatch_unknown")
        explicit_no_match = (
            response.get("person") is None
            and "credits_consumed" in response
            and not response.get("request_id")
            and str(response.get("status") or "").lower() not in {"pending", "queued", "processing"}
        )
        if explicit_no_match:
            credits = _bounded_credits(
                response.get("credits_consumed"),
                default=int(job.get("quota_units") or self.max_credits_per_lookup),
            )
            self.repo.complete_apollo_phone_no_match(
                int(job["id"]), str(job["lease_token"]), credits_consumed=credits
            )
            return ApolloPhoneRun(int(job["id"]), "no_match")
        request_id = response.get("request_id") or response.get("phone_enrichment_request_id")
        self.repo.mark_apollo_phone_awaiting_webhook(
            int(job["id"]), str(job["lease_token"]), provider_request_id=str(request_id or "") or None
        )
        return ApolloPhoneRun(int(job["id"]), "awaiting_webhook")

    def dispatch_many(self, limit: int) -> list[dict[str, Any]]:
        runs = []
        for _ in range(max(1, min(50, int(limit)))):
            run = self.dispatch_next()
            if not run:
                break
            runs.append(vars(run))
        return runs

    def process_webhook(self, job_id: int, payload: dict[str, Any], token: str) -> dict[str, Any]:
        job = self.repo.get_apollo_phone_job(job_id)
        if not job or not hmac.compare_digest(token, self.callback_token(job)):
            raise PermissionError("invalid_apollo_webhook_token")
        result = _normalize_webhook(
            payload,
            default_credits=int(job.get("quota_units") or self.max_credits_per_lookup),
        )
        return self.repo.complete_apollo_phone_webhook(
            job_id,
            credits_consumed=result["credits_consumed"],
            phone_candidates=result["phone_candidates"],
            provider_request_id=result.get("provider_request_id"),
        )

    def webhook_url(self, job: dict[str, Any]) -> str:
        base = str(self.config.raw.get("app", {}).get("public_base_url") or "").rstrip("/")
        query = urllib.parse.urlencode({"job_id": job["id"], "token": self.callback_token(job)})
        return f"{base}/webhooks/apollo?{query}"

    def callback_token(self, job: dict[str, Any]) -> str:
        secret = str(self.config.raw.get("webhooks", {}).get("apollo_secret") or "")
        message = f"apollo-phone:{job['id']}:{job['idempotency_key']}".encode("utf-8")
        return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()

    @property
    def max_credits_per_lookup(self) -> int:
        return max(1, min(9, int(self.config.raw.get("apollo_phone", {}).get("max_credits_per_lookup") or 9)))


def _identity_hash(contact: dict[str, Any]) -> str:
    identity = "|".join(
        str(contact.get(key) or "").strip().lower()
        for key in ("linkedin_url", "first_name", "last_name", "company_domain")
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _normalize_webhook(payload: dict[str, Any], *, default_credits: int = 9) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    raw_credits = payload.get("credits_consumed")
    if raw_credits is None:
        raw_credits = data.get("credits_consumed")
    credits = _bounded_credits(raw_credits, default=default_credits)
    people = payload.get("people") or data.get("people") or []
    if not people:
        person = payload.get("person") or data.get("person")
        people = [person] if isinstance(person, dict) else []
    candidates: list[dict[str, Any]] = []
    for person in people if isinstance(people, list) else []:
        if not isinstance(person, dict):
            continue
        for raw in person.get("phone_numbers") or []:
            if not isinstance(raw, dict):
                continue
            phone = str(raw.get("sanitized_number") or raw.get("raw_number") or raw.get("number") or "").strip()
            if len(re.sub(r"\D", "", phone)) < 7:
                continue
            status = str(raw.get("status_cd") or raw.get("status") or "unverified").lower()
            phone_type = str(raw.get("type_cd") or raw.get("type") or "unknown").lower()
            confidence_raw = str(raw.get("confidence_cd") or "").lower()
            confidence = {"high": 90, "medium": 75, "low": 55}.get(confidence_raw, 70 if status == "valid_number" else 60)
            candidates.append({
                "phone": phone,
                "source": "apollo_phone",
                "status": "valid" if status in {"valid", "valid_number"} else status,
                "scope": "person",
                "channel": phone_type,
                "confidence": confidence,
            })
    deduped: dict[str, dict[str, Any]] = {}
    for item in candidates:
        key = re.sub(r"\D", "", item["phone"])
        if key and int(item["confidence"]) >= int(deduped.get(key, {}).get("confidence") or -1):
            deduped[key] = item
    return {
        "credits_consumed": credits,
        "phone_candidates": list(deduped.values()),
        "provider_request_id": payload.get("request_id") or data.get("request_id"),
    }


def _bounded_credits(value: Any, *, default: int = 9) -> int:
    try:
        if value is None or isinstance(value, str) and not value.strip():
            return max(0, min(9, int(default)))
        return max(0, min(9, int(value)))
    except (TypeError, ValueError):
        return max(0, min(9, int(default)))


_BUSINESS_TZ = ZoneInfo("Asia/Shanghai")


__all__ = [
    "ApolloApiAdapter",
    "ApolloPhoneAdapter",
    "ApolloPhoneQueueService",
    "ApolloPhoneRun",
    "apollo_phone_configured",
]
