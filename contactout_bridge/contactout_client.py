from __future__ import annotations

import json
import hashlib
import re
import threading
import time
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import unquote


class ProviderError(RuntimeError):
    def __init__(self, code: str, *, retry_after_seconds: int | None = None):
        self.code = code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(code)


def _linkedin_vanity(value: Any) -> str:
    match = re.search(r"linkedin\.com/in/([^/?#\s]+)", str(value or ""), re.IGNORECASE)
    return match.group(1).strip().lower() if match else ""


def _identity(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


class ContactOutClient:
    BASE = "https://contactout.com"

    def __init__(self, *, headless: bool = False):
        self.headless = headless
        self._session: Any = None
        self._authenticated = False

    @staticmethod
    def _new_session() -> Any:
        from curl_cffi import requests as cffi_requests

        session = cffi_requests.Session(impersonate="chrome131")
        session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
            }
        )
        return session

    def login(self, email: str, password: str) -> None:
        from patchright.sync_api import sync_playwright

        session = self._new_session()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=self.headless,
                args=["--no-sandbox", "--disable-gpu", "--window-size=900,800"],
            )
            try:
                page = browser.new_page()
                page.goto(f"{self.BASE}/login", timeout=60_000, wait_until="domcontentloaded")
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    token = page.evaluate(
                        "() => document.querySelector('[name=\"cf-turnstile-response\"]')?.value || ''"
                    )
                    if token and len(token) > 10:
                        break
                    time.sleep(1)
                else:
                    raise ProviderError("challenge_required")
                page.fill('input[name="email"]', email)
                page.fill('input[name="password"]', password)
                page.click('button[type="submit"]')
                page.wait_for_timeout(4_000)
                if "/login" in page.url:
                    raise ProviderError("reauth_required")
                for cookie in page.context.cookies():
                    session.cookies.set(cookie["name"], cookie["value"], domain=cookie.get("domain", ""))
            finally:
                browser.close()
        self._session = session
        self._authenticated = True

    def restore_cookies(self, cookies: list[dict[str, str]]) -> None:
        session = self._new_session()
        for cookie in cookies:
            name = str(cookie.get("name") or "")
            value = str(cookie.get("value") or "")
            if name and value:
                session.cookies.set(
                    name,
                    value,
                    domain=str(cookie.get("domain") or ""),
                    path=str(cookie.get("path") or "/"),
                )
        self._session = session
        self._authenticated = True

    def export_cookies(self) -> list[dict[str, str]]:
        if self._session is None:
            return []
        return [
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path or "/",
            }
            for cookie in self._session.cookies.jar
        ]

    def enrich(self, contact: dict[str, Any]) -> dict[str, Any]:
        if not self._authenticated or self._session is None:
            raise ProviderError("reauth_required")
        name = " ".join(
            part.strip() for part in (str(contact.get("first_name") or ""), str(contact.get("last_name") or "")) if part.strip()
        )
        if not name:
            raise ProviderError("insufficient_identity")
        profiles = self._search(name)
        profile = self._select_profile(profiles, contact)
        if profile is None:
            return {"status": "no_match", "candidates": len(profiles)}
        result = self._reveal(str(profile.get("liVanity") or ""))
        profile_url = f"https://www.linkedin.com/in/{profile.get('liVanity')}" if profile.get("liVanity") else None
        confidence = 98 if _linkedin_vanity(contact.get("linkedin_url")) else 85
        return {
            "status": "matched",
            "match_confidence": confidence,
            "profile_url": profile_url,
            "matched_profile": {
                "full_name": profile.get("fullName"),
                "title": profile.get("title"),
                "company": profile.get("company"),
                "linkedin_url": profile_url,
            },
            "emails": [
                {
                    "email": item["value"],
                    "status": "unverified",
                    "category": "personal" if item.get("type") == "personal" else "personal_work",
                    "confidence": confidence,
                }
                for item in result.get("emails", [])
                if item.get("value")
            ],
            "phones": [
                {
                    "phone": item["value"],
                    "status": "unverified",
                    "scope": "person",
                    "channel": "mobile",
                    "confidence": confidence,
                }
                for item in result.get("phones", [])
                if item.get("value")
            ],
            "credit_remaining": result.get("credit_remaining"),
        }

    def _search(self, name: str) -> list[dict[str, Any]]:
        response = self._session.get(
            f"{self.BASE}/dashboard/search",
            params={"nm": name, "page": 1},
            headers={"Accept": "text/html,application/xhtml+xml"},
            timeout=30,
        )
        self._raise_for_status(response.status_code)
        match = re.search(r'data-page=["\']([^"\']*)["\']', response.text)
        if not match:
            return []
        try:
            data = json.loads(unescape(match.group(1)))
        except (TypeError, ValueError):
            return []
        return list(data.get("props", {}).get("results", {}).get("data", []) or [])

    @staticmethod
    def _select_profile(profiles: list[dict[str, Any]], contact: dict[str, Any]) -> dict[str, Any] | None:
        expected_vanity = _linkedin_vanity(contact.get("linkedin_url"))
        if expected_vanity:
            return next(
                (profile for profile in profiles if str(profile.get("liVanity") or "").lower() == expected_vanity),
                None,
            )
        expected_name = _identity(
            f"{contact.get('first_name') or ''} {contact.get('last_name') or ''}"
        )
        expected_company = _identity(contact.get("company_name") or contact.get("company_domain"))
        if not expected_name or not expected_company:
            return None
        matches = [
            profile
            for profile in profiles
            if _identity(profile.get("fullName")) == expected_name
            and (
                expected_company in _identity(profile.get("company"))
                or _identity(profile.get("company")) in expected_company
            )
        ]
        return matches[0] if len(matches) == 1 else None

    def _reveal(self, vanity: str) -> dict[str, Any]:
        if not vanity:
            raise ProviderError("profile_missing")
        page = self._session.get(f"{self.BASE}/dashboard/search", headers={"Accept": "text/html"}, timeout=30)
        self._raise_for_status(page.status_code)
        csrf_match = re.search(r'<meta name="csrf-token" content="([^"]+)"', page.text)
        headers = {"Content-Type": "application/json", "X-Reveal-Source": "1", "X-Requested-With": "XMLHttpRequest"}
        if csrf_match:
            headers["X-CSRF-TOKEN"] = csrf_match.group(1)
        for cookie in self._session.cookies.jar:
            if cookie.name == "XSRF-TOKEN":
                headers["X-XSRF-TOKEN"] = unquote(cookie.value)
            elif cookie.name == "_co_fp":
                headers["X-Fingerprint"] = cookie.value
        response = self._session.post(
            f"{self.BASE}/dashboard/search/reveal_profile",
            json={"liVanity": vanity},
            headers=headers,
            timeout=30,
        )
        self._raise_for_status(response.status_code)
        data = response.json()
        if int(data.get("status") or 0) != 200:
            raise ProviderError("provider_error")
        profile = data.get("profile", {})
        return {
            "emails": [
                {"value": item.get("value"), "type": "work" if item.get("type") == 1 else "personal"}
                for item in profile.get("emails", [])
                if item.get("value")
            ],
            "phones": [{"value": item.get("value")} for item in profile.get("phones", []) if item.get("value")],
            "credit_remaining": data.get("credit"),
        }

    @staticmethod
    def _raise_for_status(status_code: int) -> None:
        if status_code in {401, 403}:
            raise ProviderError("reauth_required")
        if status_code == 429:
            raise ProviderError("rate_limited", retry_after_seconds=3600)
        if status_code >= 400:
            raise ProviderError("provider_error")


class AccountPool:
    def __init__(
        self,
        credentials: dict[str, dict[str, str]],
        *,
        headless: bool = False,
        session_dir: str | None = None,
    ):
        self.credentials = credentials
        self.headless = headless
        self.session_dir = Path(session_dir) if session_dir else None
        if self.session_dir:
            self.session_dir.mkdir(parents=True, exist_ok=True)
        self._clients: dict[str, ContactOutClient] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def enrich(self, credential_ref: str, contact: dict[str, Any]) -> dict[str, Any]:
        credentials = self.credentials.get(credential_ref)
        if not credentials:
            raise ProviderError("account_unavailable")
        with self._guard:
            lock = self._locks.setdefault(credential_ref, threading.Lock())
        with lock:
            client = self._clients.get(credential_ref)
            if client is None:
                client = ContactOutClient(headless=self.headless)
                if not self._restore(client, credential_ref):
                    client.login(credentials["email"], credentials["password"])
                    self._save(client, credential_ref)
                self._clients[credential_ref] = client
            try:
                return client.enrich(contact)
            except ProviderError as exc:
                if exc.code == "reauth_required":
                    self._delete_session(credential_ref)
                    client = ContactOutClient(headless=self.headless)
                    client.login(credentials["email"], credentials["password"])
                    self._save(client, credential_ref)
                    self._clients[credential_ref] = client
                    return client.enrich(contact)
                raise

    def _session_path(self, credential_ref: str) -> Path | None:
        if not self.session_dir:
            return None
        digest = hashlib.sha256(credential_ref.encode("utf-8")).hexdigest()
        return self.session_dir / f"{digest}.json"

    def _restore(self, client: ContactOutClient, credential_ref: str) -> bool:
        path = self._session_path(credential_ref)
        if not path or not path.is_file():
            return False
        try:
            cookies = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(cookies, list) or not cookies:
                return False
            client.restore_cookies(cookies)
            return True
        except (OSError, ValueError, TypeError):
            self._delete_session(credential_ref)
            return False

    def _save(self, client: ContactOutClient, credential_ref: str) -> None:
        path = self._session_path(credential_ref)
        if not path:
            return
        path.write_text(json.dumps(client.export_cookies(), separators=(",", ":")), encoding="utf-8")
        path.chmod(0o600)

    def _delete_session(self, credential_ref: str) -> None:
        path = self._session_path(credential_ref)
        if path:
            path.unlink(missing_ok=True)
