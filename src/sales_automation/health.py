from __future__ import annotations

from typing import Any

from .production import readiness
from .sender_pool import SenderPoolManager


def check_database(repo: Any) -> dict[str, Any]:
    try:
        ok = bool(repo.db.is_available())
    except Exception as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": ok, "message": "ok" if ok else "database_unavailable"}


def check_readiness(config: Any, repo: Any) -> dict[str, Any]:
    database = check_database(repo)
    data = readiness(config)
    sender_pool = check_sender_pool(config, repo) if database["ok"] else {
        "ok": False,
        "message": "database_unavailable",
    }
    checks = [
        {"name": "database", "ok": database["ok"], "required": True, "message": database["message"]},
        *(item for item in data.get("checks", []) if item.get("name") != "database"),
        {"name": "sender_pool", "ok": sender_pool["ok"], "required": True, "message": sender_pool["message"]},
    ]
    return {
        "ready": bool(database["ok"] and data.get("ready") and sender_pool["ok"]),
        "database": database,
        "checks": checks,
    }


def check_sender_pool(config: Any, repo: Any) -> dict[str, Any]:
    try:
        sender = SenderPoolManager(config, repo).pick_sender()
    except Exception as exc:
        return {"ok": False, "message": str(exc)}
    email = str(sender.get("email") or "").strip()
    remaining = max(0, int(sender.get("daily_limit") or 0) - int(sender.get("send_count") or 0))
    return {"ok": True, "message": f"{email} available ({remaining} remaining today)"}
