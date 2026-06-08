from __future__ import annotations

from typing import Any

from .production import readiness


def check_database(repo: Any) -> dict[str, Any]:
    try:
        ok = bool(repo.db.is_available())
    except Exception as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": ok, "message": "ok" if ok else "database_unavailable"}


def check_readiness(config: Any, repo: Any) -> dict[str, Any]:
    database = check_database(repo)
    data = readiness(config)
    checks = [
        {"name": "database", "ok": database["ok"], "required": True, "message": database["message"]},
        *data.get("checks", []),
    ]
    return {"ready": bool(database["ok"] and data.get("ready")), "database": database, "checks": checks}
