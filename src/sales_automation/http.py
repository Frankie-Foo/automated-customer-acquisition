from __future__ import annotations

import json
import http.client
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


# This project must never read from the AI investment Base. Keep the guard in
# the shared HTTP client so a future integration cannot accidentally bypass it.
_BLOCKED_EXTERNAL_URL_MARKERS = (
    "ncnqnih15n0h.feishu.cn/base/CpnybxXoGasunts8O4UckKFyn5b",
    "CpnybxXoGasunts8O4UckKFyn5b",
)


def _assert_external_url_allowed(url: str) -> None:
    normalized = str(url or "").strip()
    for _ in range(3):
        decoded = unquote(normalized)
        if decoded == normalized:
            break
        normalized = decoded
    if any(marker.casefold() in normalized.casefold() for marker in _BLOCKED_EXTERNAL_URL_MARKERS):
        raise RuntimeError("Blocked external resource: AI investment Feishu Base is not available to this project")


class _SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _assert_external_url_allowed(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


urlopen = build_opener(_SafeRedirectHandler()).open


def safe_urlopen(url, *args, **kwargs):
    target = url.full_url if isinstance(url, Request) else str(url)
    _assert_external_url_allowed(target)
    return urlopen(url, *args, **kwargs)


@dataclass
class HttpClient:
    timeout: int = 30
    retries: int = 3

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        retries: int | None = None,
    ) -> dict[str, Any]:
        _assert_external_url_allowed(url)
        body = None if json_body is None else json.dumps(json_body).encode("utf-8")
        req_headers = {
            "Content-Type": "application/json",
            "User-Agent": "linkedin-sales-automation/0.1",
            **(headers or {}),
        }
        last_error: Exception | None = None
        attempt_limit = max(1, int(self.retries if retries is None else retries))
        for attempt in range(1, attempt_limit + 1):
            try:
                req = Request(url, data=body, headers=req_headers, method=method.upper())
                with safe_urlopen(req, timeout=self.timeout) as response:
                    data = response.read().decode("utf-8")
                    return json.loads(data) if data else {}
            except (HTTPError, URLError, TimeoutError, ConnectionError, http.client.IncompleteRead, json.JSONDecodeError) as exc:
                last_error = exc
                if isinstance(exc, HTTPError):
                    try:
                        detail = exc.read().decode("utf-8")
                    except Exception:
                        detail = str(exc)
                    last_error = RuntimeError(f"HTTP {exc.code}: {detail}")
                    if exc.code not in {408, 425, 429} and exc.code < 500:
                        raise RuntimeError(f"HTTP request failed: {last_error}") from exc
                if attempt == attempt_limit:
                    break
                time.sleep(2 ** (attempt - 1))
        raise RuntimeError(f"HTTP request failed after {attempt_limit} attempts: {last_error}")
