from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import UTC, datetime, timedelta
from http import cookies
from typing import Any

SESSION_COOKIE = "salesbot_session"


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 240_000)
    return "pbkdf2_sha256$240000$" + base64.b64encode(salt).decode("ascii") + "$" + base64.b64encode(digest).decode("ascii")


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def parse_session_cookie(header: str | None) -> str | None:
    if not header:
        return None
    jar = cookies.SimpleCookie()
    jar.load(header)
    morsel = jar.get(SESSION_COOKIE)
    return morsel.value if morsel else None


def session_cookie(token: str, *, max_age_seconds: int = 60 * 60 * 24 * 7, secure: bool = False) -> str:
    jar = cookies.SimpleCookie()
    jar[SESSION_COOKIE] = token
    jar[SESSION_COOKIE]["path"] = "/"
    jar[SESSION_COOKIE]["httponly"] = True
    jar[SESSION_COOKIE]["samesite"] = "Lax"
    jar[SESSION_COOKIE]["max-age"] = str(max_age_seconds)
    if secure:
        jar[SESSION_COOKIE]["secure"] = True
    return jar.output(header="").strip()


def clear_session_cookie(*, secure: bool = False) -> str:
    jar = cookies.SimpleCookie()
    jar[SESSION_COOKIE] = ""
    jar[SESSION_COOKIE]["path"] = "/"
    jar[SESSION_COOKIE]["httponly"] = True
    jar[SESSION_COOKIE]["samesite"] = "Lax"
    jar[SESSION_COOKIE]["max-age"] = "0"
    if secure:
        jar[SESSION_COOKIE]["secure"] = True
    return jar.output(header="").strip()


def default_admin_credentials() -> tuple[str, str, str]:
    username = os.environ.get("SALESBOT_ADMIN_USERNAME", "admin")
    password = os.environ.get("SALESBOT_ADMIN_PASSWORD", "admin123456")
    display_name = os.environ.get("SALESBOT_ADMIN_NAME", "Admin")
    return username, password, display_name


def session_expires_at(days: int = 7) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user["display_name"],
        "role": user["role"],
        "daily_source_limit": user["daily_source_limit"],
        "daily_send_limit": user["daily_send_limit"],
        "must_change_password": bool(user.get("must_change_password", False)),
    }
