from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


class PdcaSsoError(RuntimeError):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class PdcaProfile:
    subject: str
    username: str
    display_name: str
    role: str
    data_scope: str
    owner_key: str = ""
    team_key: str = ""
    owner_keys: tuple[str, ...] = ()


class PdcaSsoService:
    """Redeem a short-lived PDCA login ticket over a server-to-server channel."""

    def __init__(self, config: Any):
        sso = config.raw.get("sso", {}) if hasattr(config, "raw") else {}
        self.enabled = _truthy(sso.get("pdca_enabled"))
        self.exchange_url = str(sso.get("pdca_exchange_url") or "").strip()

    def exchange(self, code: str) -> PdcaProfile:
        if not self.enabled:
            raise PdcaSsoError("PDCA 单点登录未启用", status=503)
        if not _valid_exchange_url(self.exchange_url):
            raise PdcaSsoError("PDCA 单点登录地址配置无效", status=500)
        normalized = str(code or "").strip()
        if not 32 <= len(normalized) <= 256:
            raise PdcaSsoError("登录票据无效或已过期", status=401)
        request = urllib.request.Request(
            self.exchange_url,
            data=json.dumps({"code": normalized}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            mapped_status = 401 if exc.code in {400, 401, 403, 404, 422} else 503
            message = (
                "登录票据无效或已过期，请从 PDCA 重新打开客户管理"
                if mapped_status == 401 else "PDCA 认证服务暂不可用"
            )
            raise PdcaSsoError(message, status=mapped_status) from exc
        except Exception as exc:
            raise PdcaSsoError("PDCA 认证服务暂不可用", status=503) from exc
        profile = payload.get("profile") if isinstance(payload, dict) else None
        if not isinstance(payload, dict) or payload.get("ok") is not True or not isinstance(profile, dict):
            raise PdcaSsoError("登录票据无效或已过期", status=401)
        role = str(profile.get("role") or "viewer").strip().lower()
        data_scope = str(profile.get("data_scope") or "none").strip().lower()
        if role not in {"admin", "manager", "sales"}:
            raise PdcaSsoError("当前账号没有获客系统权限", status=403)
        if role == "admin" and data_scope != "all":
            raise PdcaSsoError("管理员数据范围配置无效", status=403)
        subject = str(profile.get("subject") or "").strip()
        username = str(profile.get("username") or "").strip()
        if not subject.startswith("pdca:") or not username:
            raise PdcaSsoError("PDCA 身份信息不完整", status=401)
        owner_keys = profile.get("owner_keys") if isinstance(profile.get("owner_keys"), list) else []
        return PdcaProfile(
            subject=subject,
            username=username,
            display_name=str(profile.get("display_name") or username).strip(),
            role=role,
            data_scope=data_scope,
            owner_key=str(profile.get("owner_key") or "").strip(),
            team_key=str(profile.get("team_key") or "").strip(),
            owner_keys=tuple(str(value).strip() for value in owner_keys if str(value).strip()),
        )


def _valid_exchange_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme == "https" and parsed.netloc:
        return True
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}
