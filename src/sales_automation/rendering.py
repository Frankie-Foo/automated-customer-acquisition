from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any


TOKEN_RE = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")


def render_string(template: str, values: dict[str, Any]) -> str:
    return TOKEN_RE.sub(lambda m: str(values.get(m.group(1), "")), template)


def render_template(path: Path, values: dict[str, Any]) -> tuple[str, str]:
    text = render_string(path.read_text(encoding="utf-8"), values)
    html_body = "<br>".join(html.escape(line) for line in text.splitlines())
    return text, html_body


def unsubscribe_url(contact: dict[str, Any], base_url: str) -> str:
    return f"{base_url.rstrip('/')}/unsubscribe?contact_id={contact['id']}"


def open_pixel_url(contact: dict[str, Any], step: int, base_url: str) -> str:
    return f"{base_url.rstrip('/')}/track/open?contact_id={contact['id']}&step={step}"
