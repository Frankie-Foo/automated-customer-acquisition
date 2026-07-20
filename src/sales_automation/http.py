from __future__ import annotations

import json
import http.client
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


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
                with urlopen(req, timeout=self.timeout) as response:
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
