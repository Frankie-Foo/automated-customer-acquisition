from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any


TOKEN_RE = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")


def render_string(template: str, values: dict[str, Any]) -> str:
    return TOKEN_RE.sub(lambda m: str(values.get(m.group(1), "")), template)


def render_template(path: Path, values: dict[str, Any]) -> tuple[str, str]:
    text = render_string(path.read_text(encoding="utf-8"), values)
    html_body = "<br>".join(html.escape(line) for line in text.splitlines())
    return text, html_body


def tracking_token(
    contact_id: int,
    action: str,
    secret: str,
    *,
    step: int = 0,
    expires_at: int | None = None,
) -> str:
    if not secret:
        raise ValueError("tracking_signing_secret_missing")
    payload = {
        "contact_id": int(contact_id),
        "action": str(action),
        "step": int(step),
        "exp": int(expires_at or (time.time() + 180 * 86400)),
    }
    encoded = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = _b64url(hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def verify_tracking_token(token: str, action: str, secret: str, *, now: int | None = None) -> dict[str, Any]:
    if not secret:
        raise ValueError("tracking_signing_secret_missing")
    try:
        encoded, supplied = token.split(".", 1)
        expected = _b64url(hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("invalid_tracking_signature")
        payload = json.loads(_b64url_decode(encoded).decode("utf-8"))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        if isinstance(exc, ValueError) and str(exc) in {"invalid_tracking_signature", "tracking_signing_secret_missing"}:
            raise
        raise ValueError("invalid_tracking_token") from exc
    if payload.get("action") != action:
        raise ValueError("invalid_tracking_action")
    if int(payload.get("exp") or 0) < int(now or time.time()):
        raise ValueError("tracking_token_expired")
    if int(payload.get("contact_id") or 0) <= 0:
        raise ValueError("invalid_tracking_contact")
    return payload


def unsubscribe_url(contact: dict[str, Any], base_url: str, secret: str) -> str:
    token = tracking_token(int(contact["id"]), "unsubscribe", secret)
    return f"{base_url.rstrip('/')}/unsubscribe?token={urllib.parse.quote(token)}"


def open_pixel_url(contact: dict[str, Any], step: int, base_url: str, secret: str) -> str:
    token = tracking_token(int(contact["id"]), "open", secret, step=step)
    return f"{base_url.rstrip('/')}/track/open?token={urllib.parse.quote(token)}"


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
