from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .contactout_client import AccountPool, ProviderError


MAX_BODY_BYTES = 64 * 1024


class IdempotencyStore:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.Lock()
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS responses (key_hash TEXT PRIMARY KEY, response_json TEXT NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def get(self, key: str) -> dict[str, Any] | None:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        with self.lock, self._connect() as conn:
            row = conn.execute("SELECT response_json FROM responses WHERE key_hash = ?", (digest,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, response: dict[str, Any]) -> None:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        payload = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
        with self.lock, self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO responses(key_hash, response_json) VALUES (?, ?)",
                (digest, payload),
            )


class BridgeApp:
    def __init__(self, *, key: str, pool: AccountPool, store: IdempotencyStore):
        if len(key) < 24:
            raise ValueError("CONTACTOUT_BRIDGE_KEY must contain at least 24 characters")
        self.key = key
        self.pool = pool
        self.store = store
        self._idempotency_locks = [threading.Lock() for _ in range(64)]

    def enrich(self, headers: Any, body: bytes) -> tuple[int, dict[str, Any]]:
        authorization = str(headers.get("Authorization") or "")
        if not authorization.startswith("Bearer ") or not hmac.compare_digest(authorization[7:], self.key):
            return 401, {"status": "unauthorized"}
        idempotency_key = str(headers.get("Idempotency-Key") or "").strip()
        if not idempotency_key or len(idempotency_key) > 200:
            return 400, {"status": "invalid_idempotency_key"}
        lock_index = int(hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:8], 16) % len(self._idempotency_locks)
        with self._idempotency_locks[lock_index]:
            return self._enrich_once(idempotency_key, body)

    def _enrich_once(self, idempotency_key: str, body: bytes) -> tuple[int, dict[str, Any]]:
        cached = self.store.get(idempotency_key)
        if cached is not None:
            return 200, cached
        try:
            request = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return 400, {"status": "invalid_json"}
        credential_ref = str(request.get("credential_ref") or "").strip()
        contact = request.get("contact")
        if not credential_ref or not isinstance(contact, dict):
            return 400, {"status": "invalid_request"}
        try:
            response = self.pool.enrich(credential_ref, contact)
        except ProviderError as exc:
            response = {"status": exc.code}
            if exc.retry_after_seconds:
                response["retry_after_seconds"] = exc.retry_after_seconds
            if exc.code in {"challenge_required", "reauth_required", "rate_limited"}:
                return 200, response
            return 502, response
        if response.get("status") in {"matched", "no_match"}:
            self.store.put(idempotency_key, response)
        return 200, response


def make_handler(app: BridgeApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "ContactOutBridge/1"

        def do_GET(self) -> None:
            if self.path == "/live":
                self._send(200, {"ok": True})
            elif self.path == "/ready":
                self._send(200, {"ok": True, "accounts": len(app.pool.credentials)})
            else:
                self._send(404, {"status": "not_found"})

        def do_POST(self) -> None:
            if self.path != "/enrich":
                self._send(404, {"status": "not_found"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_BODY_BYTES:
                self._send(413, {"status": "invalid_body_size"})
                return
            status, payload = app.enrich(self.headers, self.rfile.read(length))
            self._send(status, payload)

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


def load_credentials(path: str) -> dict[str, dict[str, str]]:
    file_path = Path(path)
    if not file_path.is_file():
        raise ValueError(f"credentials file not found: {path}")
    if os.name != "nt" and file_path.stat().st_mode & 0o077:
        raise ValueError("credentials file must not be readable by group or other users")
    data = json.loads(file_path.read_text(encoding="utf-8"))
    accounts = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(accounts, dict) or not accounts:
        raise ValueError("credentials file must contain a non-empty accounts object")
    normalized: dict[str, dict[str, str]] = {}
    for reference, item in accounts.items():
        if not isinstance(item, dict):
            continue
        email = str(item.get("email") or "").strip()
        password = str(item.get("password") or "")
        if reference and email and password:
            normalized[str(reference)] = {"email": email, "password": password}
    if not normalized:
        raise ValueError("credentials file contains no usable accounts")
    return normalized


def main() -> None:
    key = os.environ.get("CONTACTOUT_BRIDGE_KEY", "")
    credentials = load_credentials(os.environ.get("CONTACTOUT_CREDENTIALS_FILE", "/run/secrets/contactout-accounts.json"))
    headless = os.environ.get("CONTACTOUT_BROWSER_HEADLESS", "false").lower() in {"1", "true", "yes"}
    pool = AccountPool(
        credentials,
        headless=headless,
        session_dir=os.environ.get("CONTACTOUT_SESSION_DIR", "/data/sessions"),
    )
    store = IdempotencyStore(os.environ.get("CONTACTOUT_BRIDGE_DB", "/data/idempotency.sqlite3"))
    app = BridgeApp(key=key, pool=pool, store=store)
    host = os.environ.get("CONTACTOUT_BRIDGE_HOST", "0.0.0.0")
    port = int(os.environ.get("CONTACTOUT_BRIDGE_PORT", "8790"))
    ThreadingHTTPServer((host, port), make_handler(app)).serve_forever()


if __name__ == "__main__":
    main()
