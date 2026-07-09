from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class VpsSsoError(RuntimeError):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class VpsProfile:
    odoo_user_id: int
    name: str
    login: str
    email: str | None = None
    barcode: str | None = None
    department: str | None = None


class VpsSsoService:
    def __init__(self, config: Any):
        self.config = config
        sso = config.raw.get("sso", {}) if hasattr(config, "raw") else {}
        self.enabled = _truthy(sso.get("vps_enabled"))
        self.base_url = str(sso.get("odoo_base_url") or "").rstrip("/")

    def verify(self, *, session_id: str, user_id: int) -> VpsProfile:
        if not self.enabled:
            raise VpsSsoError("VPS SSO 未启用", status=503)
        if not self.base_url:
            raise VpsSsoError("缺少 ODOO_BASE_URL", status=500)
        if not session_id or not user_id:
            raise VpsSsoError("缺少 sessionID 或 userId", status=400)

        info = self._jsonrpc("/web/session/get_session_info", {"params": {}}, session_id=session_id)
        result = info.get("result") or {}
        uid = int(result.get("uid") or 0)
        if uid <= 0 or uid != user_id:
            raise VpsSsoError("登录已过期，请从 VPS 重新打开", status=401)

        employee = self._employee_info(uid, session_id)
        name = str(employee.get("name") or result.get("name") or result.get("display_name") or result.get("username") or f"Odoo {uid}").strip()
        login = str(result.get("username") or result.get("login") or employee.get("work_email") or f"odoo_{uid}").strip()
        email = str(employee.get("work_email") or result.get("username") or "").strip() or None
        department = _many2one_name(employee.get("department_id"))
        barcode = str(employee.get("barcode") or "").strip() or None
        return VpsProfile(odoo_user_id=uid, name=name, login=login, email=email, barcode=barcode, department=department)

    def _employee_info(self, user_id: int, session_id: str) -> dict[str, Any]:
        try:
            payload = {
                "model": "hr.employee",
                "method": "search_read",
                "args": [[["user_id", "=", user_id]]],
                "kwargs": {"fields": ["name", "barcode", "work_email", "department_id"], "limit": 1},
            }
            data = self._jsonrpc("/web/dataset/call_kw/hr.employee/search_read", {"params": payload}, session_id=session_id)
            rows = data.get("result") or []
            return rows[0] if rows else {}
        except VpsSsoError:
            return {}

    def _jsonrpc(self, path: str, payload: dict[str, Any], *, session_id: str) -> dict[str, Any]:
        body = {"jsonrpc": "2.0", "method": "call", "id": 1, **payload}
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Cookie": f"session_id={session_id}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise VpsSsoError("登录已过期，请从 VPS 重新打开", status=401) from exc
            raise VpsSsoError("认证服务暂不可用，请稍后重试", status=503) from exc
        except Exception as exc:
            raise VpsSsoError("认证服务暂不可用，请稍后重试", status=503) from exc
        if data.get("error"):
            raise VpsSsoError("登录已过期，请从 VPS 重新打开", status=401)
        return data


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _many2one_name(value: Any) -> str | None:
    if isinstance(value, list) and len(value) >= 2:
        return str(value[1])
    if isinstance(value, str):
        return value
    return None
