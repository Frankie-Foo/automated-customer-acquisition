from __future__ import annotations

import re
import hashlib
import json
import smtplib
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Protocol

from .clients import HunterClient, NinjaPearClient, ProspeoClient, is_full_email


@dataclass
class EmailCandidate:
    email: str
    source: str
    status: str
    confidence: int
    category: str
    discovered_at: str

    @classmethod
    def build(cls, email: str, source: str, status: str, confidence: int, category: str) -> "EmailCandidate":
        return cls(
            email=email.lower(),
            source=source,
            status=status or "unknown",
            confidence=max(0, min(100, int(confidence or 0))),
            category=category,
            discovered_at=datetime.now(UTC).isoformat(),
        )


class EmailProvider(Protocol):
    name: str

    def discover(self, contact: dict[str, Any], domain: str | None) -> list[EmailCandidate]:
        ...


class ExistingEmailProvider:
    name = "existing"

    def discover(self, contact: dict[str, Any], domain: str | None) -> list[EmailCandidate]:
        email = contact.get("email")
        if not is_full_email(email):
            return []
        return [EmailCandidate.build(email, self.name, contact.get("email_status") or "valid", 100, "personal_work")]


class NinjaPearEmailProvider:
    name = "ninjapear"

    def __init__(self, client: NinjaPearClient):
        self.client = client

    def discover(self, contact: dict[str, Any], domain: str | None) -> list[EmailCandidate]:
        if not domain:
            return []
        found = self.client.find_work_email(domain=domain, first_name=contact.get("first_name"), last_name=contact.get("last_name"))
        email = found.get("work_email") or found.get("email")
        if not is_full_email(email):
            return []
        return [EmailCandidate.build(email, self.name, "valid", 90, "personal_work")]


class ProspeoEmailProvider:
    name = "prospeo"

    def __init__(self, client: ProspeoClient):
        self.client = client

    def discover(self, contact: dict[str, Any], domain: str | None) -> list[EmailCandidate]:
        try:
            found = self.client.enrich_person(contact)
        except RuntimeError as exc:
            if "NO_MATCH" in str(exc):
                return []
            raise
        email_obj = found.get("email") or found.get("work_email")
        email = email_obj.get("email") if isinstance(email_obj, dict) else email_obj
        if not is_full_email(email):
            return []
        return [EmailCandidate.build(email, self.name, "valid", 95, "personal_work")]


class HunterEmailProvider:
    name = "hunter"

    def __init__(self, client: HunterClient):
        self.client = client

    def discover(self, contact: dict[str, Any], domain: str | None) -> list[EmailCandidate]:
        if not domain:
            return []
        found = self.client.find_email(domain, contact.get("first_name"), contact.get("last_name"))
        email = found.get("email")
        if not is_full_email(email):
            return []
        verified = self.client.verify_email(email)
        status = verified.get("status", "unknown")
        score = int(verified.get("score") or found.get("score") or 0)
        return [EmailCandidate.build(email, self.name, status, max(50, min(95, score or 70)), "personal_work")]


class PatternGuessHunterProvider:
    name = "pattern_guess+hunter_verify"

    def __init__(self, client: HunterClient):
        self.client = client

    def discover(self, contact: dict[str, Any], domain: str | None) -> list[EmailCandidate]:
        if not domain:
            return []
        candidates: list[EmailCandidate] = []
        for guessed in guess_email_candidates(contact, domain):
            verified = self.client.verify_email(guessed)
            status = verified.get("status", "unknown")
            score = int(verified.get("score") or 0)
            candidates.append(EmailCandidate.build(guessed, self.name, status, max(40, min(90, score or 50)), "personal_work"))
            if status == "valid":
                break
        return candidates


class PublicWebsiteEmailProvider:
    name = "public_website"

    def discover(self, contact: dict[str, Any], domain: str | None) -> list[EmailCandidate]:
        email = find_public_company_email(domain or "")
        if not email:
            return []
        return [EmailCandidate.build(email, self.name, "unverified", 30, "company_generic")]


class GitHubCommitsEmailProvider:
    name = "github"

    def __init__(self, token: str | None = None):
        self.token = token

    def discover(self, contact: dict[str, Any], domain: str | None) -> list[EmailCandidate]:
        login = _github_login(contact)
        if not login:
            return []
        url = f"https://api.github.com/users/{login}/events/public?per_page=30"
        headers = {"User-Agent": "salesbot-email-discovery/0.1"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=8) as response:
                events = json.loads(response.read(500_000).decode("utf-8"))
        except Exception:
            return []
        emails: list[EmailCandidate] = []
        for event in events if isinstance(events, list) else []:
            for commit in (event.get("payload") or {}).get("commits") or []:
                email = ((commit.get("author") or {}).get("email") or "").lower()
                if not is_full_email(email) or "noreply.github.com" in email:
                    continue
                is_work = bool(domain and email.endswith(f"@{domain}"))
                emails.append(
                    EmailCandidate.build(
                        email,
                        self.name,
                        "valid" if is_work else "unverified",
                        80 if is_work else 65,
                        "personal_work" if is_work else "personal_free",
                    )
                )
        return emails


class GravatarEmailProvider:
    name = "gravatar"

    def discover(self, contact: dict[str, Any], domain: str | None) -> list[EmailCandidate]:
        if not domain:
            return []
        candidates: list[EmailCandidate] = []
        for guessed in guess_email_candidates(contact, domain):
            digest = hashlib.md5(guessed.strip().lower().encode("utf-8")).hexdigest()
            url = f"https://www.gravatar.com/avatar/{digest}?d=404"
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "salesbot-email-discovery/0.1"})
                with urllib.request.urlopen(request, timeout=5) as response:
                    if response.status == 200:
                        candidates.append(EmailCandidate.build(guessed, self.name, "unverified", 65, "personal_work"))
            except Exception:
                continue
        return candidates


class SmtpVerifyProvider:
    name = "smtp_verify"

    def discover(self, contact: dict[str, Any], domain: str | None) -> list[EmailCandidate]:
        if not domain:
            return []
        candidates: list[EmailCandidate] = []
        for guessed in guess_email_candidates(contact, domain)[:5]:
            status = self._verify(guessed, domain)
            confidence = 70 if status == "valid" else 35
            candidates.append(EmailCandidate.build(guessed, self.name, status, confidence, "personal_work"))
            if status == "valid":
                break
        return candidates

    def _verify(self, email: str, domain: str) -> str:
        try:
            with smtplib.SMTP(domain, timeout=10) as smtp:
                smtp.ehlo_or_helo_if_needed()
                smtp.mail("")
                code, _ = smtp.rcpt(email)
        except Exception:
            return "unverified"
        if 200 <= int(code) < 300:
            return "valid"
        if int(code) in {550, 551, 553}:
            return "risky"
        return "unverified"


class EmailDiscoveryEngine:
    def __init__(
        self,
        providers: list[EmailProvider],
        *,
        max_candidates: int = 10,
        stats_recorder: Callable[..., None] | None = None,
    ):
        self.providers = providers
        self.max_candidates = max_candidates
        self.stats_recorder = stats_recorder

    def discover(self, contact: dict[str, Any], domain: str | None) -> dict[str, Any]:
        all_candidates: list[EmailCandidate] = []
        selected: EmailCandidate | None = None
        for provider in self.providers:
            provider_name = _provider_name(provider)
            try:
                candidates = provider.discover(contact, domain)
            except Exception as exc:
                self._record_provider(provider_name, calls=1, errors=1, last_error=str(exc)[:500])
                raise
            all_candidates.extend(candidates)
            provider_selected = _select_valid_personal(candidates)
            selected = selected or provider_selected
            self._record_provider(
                provider_name,
                calls=1,
                candidates=len(candidates),
                valid_candidates=sum(1 for item in candidates if item.status == "valid"),
                selected=1 if provider_selected else 0,
                credits_used=_provider_credit_cost(provider_name),
            )
        fields: dict[str, Any] = {"email_status": "unknown"}
        if selected:
            fields.update(
                {
                    "email": selected.email,
                    "email_status": selected.status,
                    "email_source": selected.source,
                    "email_confidence": selected.confidence,
                }
            )
        if all_candidates:
            fields["email_candidates"] = [asdict(candidate) for candidate in _dedupe_candidates(all_candidates)[: self.max_candidates]]
        return fields

    def _record_provider(self, provider: str, **fields: Any) -> None:
        if not self.stats_recorder:
            return
        try:
            self.stats_recorder(provider, **fields)
        except Exception:
            pass


def build_email_discovery_engine(config: Any, *, stats_recorder: Callable[..., None] | None = None) -> EmailDiscoveryEngine:
    apis = config.apis
    discovery_cfg = config.raw.get("email_discovery", {})
    provider_names = discovery_cfg.get("providers") or ["prospeo", "ninjapear", "hunter", "pattern_guess", "public_website"]
    max_candidates = int(discovery_cfg.get("max_candidates") or 10)
    hunter = HunterClient(apis.get("hunter_key", "")) if apis.get("hunter_key") else None
    ninjapear = NinjaPearClient(apis.get("ninjapear_key", "")) if apis.get("ninjapear_key") else None
    prospeo = ProspeoClient(apis.get("prospeo_key", "")) if apis.get("prospeo_key") else None
    providers: list[EmailProvider] = [ExistingEmailProvider()]
    for name in provider_names:
        if name == "prospeo" and prospeo:
            providers.append(ProspeoEmailProvider(prospeo))
        elif name == "ninjapear" and ninjapear:
            providers.append(NinjaPearEmailProvider(ninjapear))
        elif name == "hunter" and hunter:
            providers.append(HunterEmailProvider(hunter))
        elif name == "pattern_guess" and hunter:
            providers.append(PatternGuessHunterProvider(hunter))
        elif name == "github":
            providers.append(GitHubCommitsEmailProvider(apis.get("github_token")))
        elif name == "gravatar":
            providers.append(GravatarEmailProvider())
        elif name == "smtp_verify" and discovery_cfg.get("smtp_verify_enabled") is True:
            providers.append(SmtpVerifyProvider())
        elif name == "public_website":
            providers.append(PublicWebsiteEmailProvider())
    return EmailDiscoveryEngine(providers, max_candidates=max_candidates, stats_recorder=stats_recorder)


def _provider_credit_cost(provider: str) -> int:
    return 1 if provider in {"prospeo", "ninjapear", "hunter", "pattern_guess"} else 0


def _provider_name(provider: EmailProvider) -> str:
    return str(getattr(provider, "name", provider.__class__.__name__))


def _select_valid_personal(candidates: list[EmailCandidate]) -> EmailCandidate | None:
    valid = [
        candidate
        for candidate in candidates
        if candidate.status == "valid" and candidate.category == "personal_work" and is_full_email(candidate.email)
    ]
    return sorted(valid, key=lambda item: item.confidence, reverse=True)[0] if valid else None


def _dedupe_candidates(candidates: list[EmailCandidate]) -> list[EmailCandidate]:
    by_email: dict[str, EmailCandidate] = {}
    for candidate in candidates:
        current = by_email.get(candidate.email)
        if current is None or candidate.confidence > current.confidence:
            by_email[candidate.email] = candidate
    return sorted(by_email.values(), key=lambda item: item.confidence, reverse=True)


def _github_login(contact: dict[str, Any]) -> str | None:
    profiles = contact.get("social_profiles") if isinstance(contact.get("social_profiles"), dict) else {}
    url = profiles.get("github") or contact.get("github_url")
    if not url:
        return None
    match = re.search(r"github\.com/([^/?#]+)", str(url))
    return match.group(1) if match else str(url).strip("@/")


def guess_email_candidates(contact: dict[str, Any], domain: str) -> list[str]:
    first = _email_name_part(contact.get("first_name"))
    last = _email_name_part(contact.get("last_name"))
    if not first or not last or not domain:
        return []
    patterns = [
        f"{first}.{last}@{domain}",
        f"{first}{last}@{domain}",
        f"{first[0]}{last}@{domain}",
        f"{first}@{domain}",
        f"{last}.{first}@{domain}",
    ]
    return list(dict.fromkeys(patterns))


def _email_name_part(value: str | None) -> str:
    return re.sub(r"[^a-zA-Z]", "", value or "").lower()


def find_public_company_email(domain: str) -> str | None:
    if not domain:
        return None
    for path in ("", "/contact", "/contact-us", "/about", "/team"):
        for scheme in ("https", "http"):
            url = f"{scheme}://{domain}{path}"
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "salesbot-email-discovery/0.1"})
                with urllib.request.urlopen(request, timeout=5) as response:
                    content_type = response.headers.get("Content-Type", "")
                    if "text/html" not in content_type and "text/plain" not in content_type:
                        continue
                    text = response.read(300_000).decode("utf-8", errors="ignore")
                email = pick_public_email(text, domain)
                if email:
                    return email
            except Exception:
                continue
    return None


def pick_public_email(text: str, domain: str) -> str | None:
    emails = {
        match.group(0).lower()
        for match in re.finditer(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", text, flags=re.I)
    }
    blocked_prefixes = {"privacy", "legal", "support", "help", "info", "press", "media", "noreply", "no-reply"}
    same_domain = [email for email in emails if email.endswith(f"@{domain}") and email.split("@", 1)[0] not in blocked_prefixes]
    if same_domain:
        return sorted(same_domain)[0]
    fallback = [email for email in emails if email.split("@", 1)[0] not in blocked_prefixes]
    return sorted(fallback)[0] if fallback else None
