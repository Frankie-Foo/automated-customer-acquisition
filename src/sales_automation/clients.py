from __future__ import annotations

import urllib.parse
from typing import Any

from .http import HttpClient


class ProxycurlClient:
    def __init__(self, api_key: str, http: HttpClient | None = None):
        self.api_key = api_key
        self.http = http or HttpClient()

    def search_people(self, criteria: dict[str, Any], limit: int) -> list[dict[str, Any]]:
        params = {
            "page_size": min(limit, 100),
            "role": criteria.get("title", ""),
            "industry": criteria.get("industry", ""),
            "country": criteria.get("location", ""),
        }
        url = "https://nubela.co/proxycurl/api/search/person/?" + urllib.parse.urlencode(params)
        data = self.http.request("GET", url, headers={"Authorization": f"Bearer {self.api_key}"})
        people = data.get("results") or data.get("data") or []
        return [_normalize_proxycurl_person(item, criteria) for item in people[:limit]]

    def company_lookup(self, domain: str) -> dict[str, Any]:
        url = "https://nubela.co/proxycurl/api/linkedin/company/resolve?" + urllib.parse.urlencode({"company_domain": domain})
        return self.http.request("GET", url, headers={"Authorization": f"Bearer {self.api_key}"})


class NinjaPearClient:
    def __init__(self, api_key: str, http: HttpClient | None = None):
        self.api_key = api_key
        self.http = http or HttpClient(timeout=60)

    def search_employees(self, *, company_website: str, role: str, location: str | None = None, limit: int = 25) -> list[dict[str, Any]]:
        params = {"company_website": company_website, "role": role}
        if location:
            params["location"] = location
        url = "https://nubela.co/api/v1/employee/search?" + urllib.parse.urlencode(params)
        data = self.http.request("GET", url, headers={"Authorization": f"Bearer {self.api_key}"})
        employees = data.get("employees") or data.get("data") or data.get("results") or []
        return [_normalize_ninjapear_employee(item, company_website, role) for item in employees[:limit]]

    def find_work_email(self, *, domain: str, first_name: str | None, last_name: str | None = None) -> dict[str, Any]:
        params = {"domain": domain, "first_name": first_name or ""}
        if last_name:
            params["last_name"] = last_name
        url = "https://nubela.co/api/v1/employee/work-email?" + urllib.parse.urlencode(params)
        return self.http.request("GET", url, headers={"Authorization": f"Bearer {self.api_key}"})


class ProspeoClient:
    def __init__(self, api_key: str, http: HttpClient | None = None):
        self.api_key = api_key
        self.http = http or HttpClient(timeout=60)

    def search_people(self, *, company_website: str | None, role: str, industry: str | None = None, location: str | None = None, limit: int = 25) -> list[dict[str, Any]]:
        filters: dict[str, Any] = {
            "person_job_title": {"include": [role]},
        }
        if company_website:
            filters["company"] = {"websites": {"include": [_domain_from_website(company_website)]}}
        # Prospeo validates industry/location against exact internal values.
        # Keep the first production path conservative: company + title only.
        body = {"page": 1, "filters": filters}
        data = self.http.request(
            "POST",
            "https://api.prospeo.io/search-person",
            headers={"X-KEY": self.api_key},
            json_body={key: value for key, value in body.items() if value},
        )
        people = data.get("data") or data.get("results") or []
        if isinstance(people, dict):
            people = people.get("results") or people.get("items") or []
        return [_normalize_prospeo_person(item, company_website, role) for item in people[:limit]]

    def enrich_person(self, contact: dict[str, Any]) -> dict[str, Any]:
        data = {
            "person_id": contact.get("source_person_id"),
            "first_name": contact.get("first_name"),
            "last_name": contact.get("last_name"),
            "company_website": contact.get("company_domain"),
            "linkedin_url": contact.get("linkedin_url") if str(contact.get("linkedin_url", "")).startswith("http") else None,
        }
        body = {"only_verified_email": True, "enrich_mobile": False, "data": {key: value for key, value in data.items() if value}}
        data = self.http.request(
            "POST",
            "https://api.prospeo.io/enrich-person",
            headers={"X-KEY": self.api_key},
            json_body={key: value for key, value in body.items() if value},
        )
        return data.get("data") or data


class PeopleDataLabsClient:
    def __init__(self, api_key: str, http: HttpClient | None = None):
        self.api_key = api_key
        self.http = http or HttpClient(timeout=45)

    def enrich_person(self, contact: dict[str, Any]) -> dict[str, Any]:
        params = {"api_key": self.api_key, "pretty": "false"}
        linkedin_url = contact.get("linkedin_url")
        if str(linkedin_url or "").startswith("http"):
            params["profile"] = linkedin_url
        elif is_full_email(contact.get("email")):
            params["email"] = contact["email"]
        else:
            if contact.get("first_name"):
                params["first_name"] = contact["first_name"]
            if contact.get("last_name"):
                params["last_name"] = contact["last_name"]
            if contact.get("company_name"):
                params["company"] = contact["company_name"]
            if contact.get("company_domain"):
                params["company_domain"] = contact["company_domain"]
        if len(params) <= 2:
            raise RuntimeError("PDL enrichment requires LinkedIn URL, email, or name + company")
        url = "https://api.peopledatalabs.com/v5/person/enrich?" + urllib.parse.urlencode(params)
        data = self.http.request("GET", url)
        return data.get("data") or data


class PeopleDBClient:
    def __init__(self, api_key: str, http: HttpClient | None = None):
        self.api_key = api_key
        self.http = http or HttpClient(timeout=45)

    def enrich_person(self, contact: dict[str, Any]) -> dict[str, Any]:
        params: dict[str, Any] = {}
        linkedin_id = _linkedin_public_identifier(contact.get("linkedin_url"))
        if linkedin_id:
            params["linkedin_public_identifier"] = linkedin_id
        social_profiles = contact.get("social_profiles") if isinstance(contact.get("social_profiles"), dict) else {}
        github_login = _github_login(social_profiles.get("github"))
        if github_login:
            params["github_login"] = github_login
        if not params:
            raise RuntimeError("PeopleDB enrichment requires a LinkedIn public identifier or GitHub login")
        url = "https://peopledb.co/api/v1/people?" + urllib.parse.urlencode(params)
        return self.http.request(
            "GET",
            url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "X-API-Key": self.api_key,
            },
        )


class HunterClient:
    def __init__(self, api_key: str, http: HttpClient | None = None):
        self.api_key = api_key
        self.http = http or HttpClient()

    def find_email(self, domain: str, first_name: str | None, last_name: str | None) -> dict[str, Any]:
        params = {
            "domain": domain,
            "first_name": first_name or "",
            "last_name": last_name or "",
            "api_key": self.api_key,
        }
        url = "https://api.hunter.io/v2/email-finder?" + urllib.parse.urlencode(params)
        data = self.http.request("GET", url)
        return data.get("data", {})

    def verify_email(self, email: str) -> dict[str, Any]:
        params = {"email": email, "api_key": self.api_key}
        url = "https://api.hunter.io/v2/email-verifier?" + urllib.parse.urlencode(params)
        data = self.http.request("GET", url)
        return data.get("data", {})


class LLMClient:
    def __init__(
        self,
        api_key: str,
        *,
        provider: str = "deepseek",
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
        http: HttpClient | None = None,
    ):
        self.api_key = api_key
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.http = http or HttpClient()

    def opener(self, contact: dict[str, Any]) -> str:
        company = contact.get("company_name") or "your company"
        context = _contact_source_context(contact)
        reason = context.get("seed_reason") or ""
        category = context.get("seed_category") or contact.get("industry") or ""
        fallback = _fallback_opener(contact, reason=reason, category=category)
        if not self.api_key:
            return fallback
        if contact.get("_fallback"):
            return fallback
        prompt = (
            "Write exactly one concise, specific, non-hype cold email opening sentence. "
            "You are the sender writing to the recipient; never claim to work at or lead the recipient company. "
            "Use only the provided fields. Do not use placeholders, brackets, invented competitors, invented tools, or invented facts. "
            "Do not mention launches, funding, hiring, growth, tools, competitors, case studies, or recent events. "
            "If an account research note is provided, use it only as a plain observation and do not add claims beyond it. "
            "A safe pattern is: 'I noticed {company} is in {category} and your role touches that area, so I thought this might be relevant.' "
            f"Recipient role: {contact.get('job_title')}. Recipient company: {company}. "
            f"Industry/category: {category}. Account research note: {reason}. "
            f"LinkedIn URL: {contact.get('linkedin_url')}. Do not invent facts."
        )
        if self.provider == "openai":
            data = self.http.request(
                "POST",
                f"{self.base_url or 'https://api.openai.com/v1'}/responses",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json_body={"model": self.model or "gpt-4o-mini", "input": prompt, "max_output_tokens": 60},
            )
            return _guard_opener(_extract_response_text(data), fallback)
        data = self.http.request(
            "POST",
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json_body={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "You write concise B2B cold email opening sentences. Do not invent facts."},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 60,
                "temperature": 0.4,
            },
        )
        return _guard_opener(_extract_chat_text(data), fallback)


OpenAIClient = LLMClient


class MailClient:
    def __init__(self, provider: str, api_key: str, sender: dict[str, Any], http: HttpClient | None = None):
        self.provider = provider
        self.api_key = api_key
        self.sender = sender
        self.http = http or HttpClient()

    def send(
        self,
        to_email: str,
        subject: str,
        html: str,
        text: str,
        *,
        metadata: dict[str, Any] | None = None,
        reply_to: str | None = None,
    ) -> str | None:
        if self.sender.get("dry_run", True):
            return f"dry-run:{to_email}:{subject}"
        if self.provider == "resend":
            tags = []
            for key, value in (metadata or {}).items():
                tags.append({"name": str(key).replace("-", "_")[:256], "value": str(value).replace(" ", "_")[:256]})
            payload = {
                "from": f"{self.sender.get('name')} <{self.sender.get('email')}>",
                "to": [to_email],
                "subject": subject,
                "html": html,
                "text": text,
                "tags": tags,
            }
            if reply_to:
                payload["reply_to"] = [reply_to]
            data = self.http.request(
                "POST",
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json_body=payload,
            )
            return data.get("id")
        if self.provider == "sendgrid":
            payload = {
                "personalizations": [{"to": [{"email": to_email}]}],
                "from": {"email": self.sender.get("email"), "name": self.sender.get("name")},
                "subject": subject,
                "content": [{"type": "text/plain", "value": text}, {"type": "text/html", "value": html}],
            }
            if reply_to:
                payload["reply_to"] = {"email": reply_to}
            data = self.http.request(
                "POST",
                "https://api.sendgrid.com/v3/mail/send",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json_body=payload,
            )
            return data.get("id")
        raise ValueError(f"Unsupported mail provider: {self.provider}")


class SlackClient:
    def __init__(self, webhook_url: str | None, http: HttpClient | None = None):
        self.webhook_url = webhook_url
        self.http = http or HttpClient()

    def notify(self, text: str) -> None:
        if self.webhook_url:
            self.http.request("POST", self.webhook_url, json_body={"text": text})


def _normalize_proxycurl_person(item: dict[str, Any], criteria: dict[str, Any]) -> dict[str, Any]:
    return {
        "linkedin_url": item.get("linkedin_profile_url") or item.get("profile_url") or item.get("linkedin_url"),
        "first_name": item.get("first_name"),
        "last_name": item.get("last_name"),
        "job_title": item.get("occupation") or item.get("job_title") or criteria.get("title"),
        "company_name": item.get("current_company") or item.get("company_name"),
        "company_domain": item.get("company_domain"),
        "industry": item.get("industry") or criteria.get("industry"),
        "location": item.get("location") or criteria.get("location"),
        "company_size": item.get("company_size"),
        "source": "proxycurl_search",
    }


def _normalize_ninjapear_employee(item: dict[str, Any], company_website: str, role: str) -> dict[str, Any]:
    full_name = item.get("full_name") or item.get("name") or ""
    first_name = item.get("first_name")
    last_name = item.get("last_name")
    if not first_name and full_name:
        parts = full_name.split()
        first_name = parts[0]
        last_name = " ".join(parts[1:]) or None
    linkedin_url = item.get("linkedin_url") or item.get("linkedin_profile_url") or item.get("profile_url")
    if not linkedin_url:
        synthetic_id = item.get("id") or item.get("work_email") or f"{company_website}:{full_name}:{role}"
        linkedin_url = f"ninjapear://employee/{urllib.parse.quote(str(synthetic_id), safe='')}"
    domain = _domain_from_website(item.get("company_website") or item.get("company_domain") or company_website)
    return {
        "linkedin_url": linkedin_url,
        "source_person_id": item.get("person_id") or item.get("id"),
        "first_name": first_name,
        "last_name": last_name,
        "email": item.get("work_email") or item.get("email"),
        "email_status": "valid" if item.get("work_email") or item.get("email") else "unknown",
        "job_title": item.get("role") or item.get("job_title") or role,
        "company_name": item.get("company_name") or item.get("company") or domain,
        "company_domain": domain,
        "industry": item.get("industry"),
        "location": item.get("location"),
        "source": "ninjapear_employee_search",
    }


def _normalize_prospeo_person(item: dict[str, Any], company_website: str | None, role: str) -> dict[str, Any]:
    person = item.get("person") if isinstance(item.get("person"), dict) else item
    company = item.get("company") if isinstance(item.get("company"), dict) else {}
    first_name = person.get("first_name")
    last_name = person.get("last_name")
    full_name = person.get("full_name") or person.get("name") or ""
    if not first_name and full_name:
        parts = full_name.split()
        first_name = parts[0]
        last_name = " ".join(parts[1:]) or None
    domain = _domain_from_website(company.get("website") or company.get("domain") or person.get("company_domain") or person.get("company_website") or company_website)
    linkedin_url = person.get("linkedin_url") or person.get("linkedin")
    if not linkedin_url:
        seed = person.get("id") or person.get("email") or f"{domain}:{full_name}:{role}"
        linkedin_url = f"prospeo://person/{urllib.parse.quote(str(seed), safe='')}"
    email_obj = person.get("email") or person.get("work_email")
    email = email_obj.get("email") if isinstance(email_obj, dict) else email_obj
    if isinstance(email, str) and "*" in email:
        email = None
    location_obj = person.get("location")
    if isinstance(location_obj, dict):
        location = ", ".join(str(location_obj.get(key)) for key in ("city", "state", "country") if location_obj.get(key))
    else:
        location = location_obj
    return {
        "linkedin_url": linkedin_url,
        "source_person_id": person.get("person_id") or person.get("id"),
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "email_status": "valid" if isinstance(email, str) and email else "unknown",
        "job_title": person.get("job_title") or person.get("title") or role,
        "company_name": company.get("name") or person.get("company_name") or domain,
        "company_domain": domain,
        "industry": company.get("industry") or person.get("industry"),
        "location": location,
        "source": "prospeo_person_search",
    }


def is_full_email(value: Any) -> bool:
    return isinstance(value, str) and "@" in value and "*" not in value


def _domain_from_website(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urllib.parse.urlparse(value if "://" in value else f"https://{value}")
    host = (parsed.netloc or parsed.path or value).split("/")[0].strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _linkedin_public_identifier(value: str | None) -> str | None:
    if not value or "linkedin.com/in/" not in value:
        return None
    parsed = urllib.parse.urlparse(value if "://" in value else f"https://{value}")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0].lower() == "in":
        return parts[1]
    return None


def _github_login(value: str | None) -> str | None:
    if not value:
        return None
    if "github.com/" not in value:
        return value.strip().strip("/") or None
    parsed = urllib.parse.urlparse(value if "://" in value else f"https://{value}")
    parts = [part for part in parsed.path.split("/") if part]
    return parts[0] if parts else None


def _extract_response_text(data: dict[str, Any]) -> str:
    if data.get("output_text"):
        return str(data["output_text"]).strip()
    chunks: list[str] = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"}:
                chunks.append(content.get("text", ""))
    return " ".join(chunks).strip()


def _extract_chat_text(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return str(message.get("content") or "").strip()


def _contact_source_context(contact: dict[str, Any]) -> dict[str, str]:
    context = contact.get("source_context")
    if not isinstance(context, dict):
        return {}
    return {str(key): str(value).strip() for key, value in context.items() if str(value or "").strip()}


def _fallback_opener(contact: dict[str, Any], *, reason: str = "", category: str = "") -> str:
    company = contact.get("company_name") or "your company"
    role = contact.get("job_title") or "your role"
    if reason:
        clean_reason = " ".join(str(reason).split())
        if len(clean_reason) > 170:
            clean_reason = clean_reason[:167].rstrip() + "..."
        return f"I noticed {company} in our account research: {clean_reason}"
    if category:
        return f"I noticed {company} is relevant to {category} and thought this might be worth a quick conversation."
    return f"I noticed your work as {role} at {company} and thought this might be relevant."


def _guard_opener(text: str, fallback: str) -> str:
    text = text.strip().strip('"')
    risky = [
        "[", "]", "{{", "}}", "launched", "funding", "raised", "hiring", "competitor",
        "uses ", "case study", "recent", "i'm the founder", "i am the founder", "our saas",
        "our platform", "our approach",
    ]
    if not text or any(token.lower() in text.lower() for token in risky):
        return fallback
    return text
