#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path


ALLOWED_KEYS = {
    "MAIL_PROVIDER",
    "MAIL_FROM_EMAIL",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_SECURITY",
    "SMTP_ENVELOPE_FROM",
    "SMTP_ALLOW_FROM_ALIAS",
    "IMAP_HOST",
    "IMAP_PORT",
    "IMAP_USER",
    "IMAP_PASSWORD",
    "IMAP_FOLDER",
    "IMAP_LOOKBACK_DAYS",
    "MAILBOX_POLL_INTERVAL_SECONDS",
    "OUTBOUND_IDENTITY_MODE",
}


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: update-mailbox-env.py /path/to/.env")
    target = Path(sys.argv[1])
    updates = _read_updates(sys.stdin.read())
    required = {"SMTP_USER", "SMTP_PASSWORD", "IMAP_USER", "IMAP_PASSWORD"}
    missing = sorted(key for key in required if not updates.get(key))
    if missing:
        raise SystemExit(f"missing mailbox settings: {', '.join(missing)}")

    original = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
    output: list[str] = []
    written: set[str] = set()
    for line in original:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else ""
        if key in updates:
            output.append(f"{key}={updates[key]}")
            written.add(key)
        else:
            output.append(line)
    if output and output[-1]:
        output.append("")
    output.extend(f"{key}={value}" for key, value in updates.items() if key not in written)

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(target)
    os.chmod(target, 0o600)
    print(f"updated {len(updates)} mailbox settings in {target}")
    return 0


def _read_updates(raw: str) -> dict[str, str]:
    updates: dict[str, str] = {}
    for line in raw.splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in ALLOWED_KEYS:
            raise SystemExit(f"unsupported mailbox setting: {key}")
        if "\n" in value or "\r" in value:
            raise SystemExit(f"invalid value for {key}")
        updates[key] = value
    return updates


if __name__ == "__main__":
    raise SystemExit(main())
