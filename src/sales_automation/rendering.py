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


def build_html_body(text: str, *, product_images: dict[str, Any] | None = None) -> str:
    """Build email HTML body from plain text, optionally appending product images."""
    html_body = "<br>".join(html.escape(line) for line in str(text).splitlines())
    if product_images and product_images.get("enabled"):
        image_html = build_product_image_html(product_images)
        if image_html:
            html_body += image_html
    return html_body


def build_product_image_html(config: dict[str, Any]) -> str:
    """Build a responsive product image grid for HTML email body."""
    items = config.get("items") if isinstance(config.get("items"), list) else []
    if not items:
        return ""
    base_url = str(config.get("base_url") or "").rstrip("/")
    cols = max(1, min(5, int(config.get("columns") or 3)))
    parts = [
        '<div style="margin-top:32px;padding-top:24px;border-top:1px solid #e8e8e8;font-family:Arial,Helvetica,sans-serif">',
        '<p style="margin:0 0 16px 0;font-size:13px;color:#555;line-height:1.6">',
        'Explore the VERTU product portfolio:' if cols >= 3 else 'Featured VERTU products:',
        '</p>',
        '<table border="0" cellpadding="0" cellspacing="0" width="100%" role="presentation" style="border-collapse:collapse">',
    ]
    for i, item in enumerate(items):
        if isinstance(item, dict) and item.get("src"):
            src = str(item["src"])
            if base_url and not src.startswith(("http://", "https://")):
                src = f"{base_url}/{src.lstrip('/')}"
            alt = html.escape(str(item.get("alt") or "VERTU product"), quote=True)
            width = max(160, min(640, int(item.get("width") or 320)))
            if i % cols == 0:
                parts.append('<tr>')
            parts.append(
                f'<td valign="top" style="padding:0 8px 16px 0" width="{100 // cols}%">'
                f'<img src="{html.escape(src, quote=True)}" alt="{alt}" '
                f'width="{width}" style="max-width:100%;height:auto;display:block;border:1px solid #eee;border-radius:4px" />'
                f'</td>'
            )
            if (i + 1) % cols == 0 or i == len(items) - 1:
                parts.append('</tr>')
    parts.append('</table>')
    parts.append('<p style="margin:8px 0 0 0;font-size:11px;color:#aaa">')
    parts.append('AI Phones · Smartwatches · Mechanical Watches · Premium Wearables')
    parts.append('</p>')
    parts.append('</div>')
    return "\n".join(parts)


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
