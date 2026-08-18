from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from .contactout_client import ContactOutClient, ProviderError


def load_credentials(path: Path) -> dict[str, dict[str, str]]:
    text = path.read_text(encoding="utf-8-sig")
    if text.lstrip().startswith("{"):
        data = json.loads(text)
        accounts = data.get("accounts", data)
        if not isinstance(accounts, dict):
            raise ValueError("credential file must contain an accounts object")
        return {
            str(key): {"email": str(value["email"]), "password": str(value["password"])}
            for key, value in accounts.items()
        }

    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if len(lines) % 2:
        raise ValueError("plain credential file must contain alternating email/password lines")
    accounts = {}
    for index, offset in enumerate(range(0, len(lines), 2), start=1):
        email_match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", lines[offset], re.IGNORECASE)
        if not email_match:
            raise ValueError(f"credential pair {index} has no valid email")
        password = re.sub(r"^(?:密码|password)\s*[:：=]\s*", "", lines[offset + 1], flags=re.IGNORECASE)
        if not password:
            raise ValueError(f"credential pair {index} has no password")
        accounts[f"contactout-account-{index:02d}"] = {
            "email": email_match.group(0),
            "password": password,
        }
    return accounts


def session_path(session_dir: Path, credential_ref: str) -> Path:
    digest = hashlib.sha256(credential_ref.encode("utf-8")).hexdigest()
    return session_dir / f"{digest}.json"


def bootstrap_account(
    credential_ref: str,
    credentials: dict[str, str],
    session_dir: Path,
    *,
    timeout_seconds: int,
) -> Path:
    client = ContactOutClient(headless=False)
    client.login(credentials["email"], credentials["password"], timeout_seconds=timeout_seconds)
    cookies = client.export_cookies()
    if not cookies:
        raise ProviderError("reauth_required")
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_path(session_dir, credential_ref)
    path.write_text(json.dumps(cookies, separators=(",", ":")), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap authorized ContactOut browser sessions")
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--session-dir", type=Path, required=True)
    parser.add_argument("--account", action="append", dest="accounts")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    credentials = load_credentials(args.credentials)
    selected = args.accounts or list(credentials)
    failures = 0
    for credential_ref in selected:
        account = credentials.get(credential_ref)
        if not account:
            print(f"{credential_ref}: missing")
            failures += 1
            continue
        masked = account["email"].split("@", 1)
        identity = f"{masked[0][:2]}***@{masked[1]}" if len(masked) == 2 else "***"
        print(f"{credential_ref}: opening visible login for {identity}")
        try:
            path = bootstrap_account(
                credential_ref,
                account,
                args.session_dir,
                timeout_seconds=max(30, args.timeout),
            )
            print(f"{credential_ref}: session saved to {path}")
        except (ProviderError, OSError, ValueError) as exc:
            print(f"{credential_ref}: failed ({exc})")
            failures += 1
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
