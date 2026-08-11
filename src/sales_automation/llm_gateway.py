from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from .config import AppConfig
from .http import HttpClient
from .logging_utils import log


class LLMGateway:
    """Small policy boundary for every runtime LLM completion."""

    def __init__(self, config: AppConfig, repo: Any, *, http: HttpClient | None = None):
        self.config = config
        self.repo = repo
        self.http = http or HttpClient()

    def complete(
        self,
        *,
        operation: str,
        messages: list[dict[str, str]],
        contact: dict[str, Any] | None = None,
        max_tokens: int,
        temperature: float,
        validate: Callable[[str], str | None],
    ) -> str | None:
        settings = self._settings()
        provider = str(settings["provider"])
        model = str(settings["model"])
        input_chars = sum(len(str(item.get("content") or "")) for item in messages)
        if not self._allowed(settings, contact):
            self._log(provider, model, calls=0, input_chars=0, output_chars=0, cache_hit=False)
            return None
        owner_user_id = _owner_user_id(contact)
        cache_key = self.cache_key(
            operation=operation,
            provider=provider,
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            owner_user_id=owner_user_id,
            cache_version=str(settings["cache_version"]),
        )
        try:
            cached = self._cached(cache_key, owner_user_id)
        except Exception as exc:
            log("llm.gateway_cache_failed", operation=operation, error=type(exc).__name__)
            return None
        if cached is not None:
            self._log(provider, model, calls=0, input_chars=0, output_chars=0, cache_hit=True)
            return cached
        api_key = self._api_key(provider)
        if not api_key:
            self._log(provider, model, calls=0, input_chars=0, output_chars=0, cache_hit=False)
            return None
        try:
            allowed = self._reserve(provider, model, input_chars, max_tokens * 4, settings)
        except Exception as exc:
            log("llm.gateway_budget_failed", operation=operation, error=type(exc).__name__)
            return None
        if not allowed:
            self._log(provider, model, calls=0, input_chars=0, output_chars=0, cache_hit=False)
            return None
        try:
            raw_text = self._request(provider, model, str(settings["base_url"]), api_key, messages, max_tokens, temperature)
            text = validate(raw_text) if raw_text else None
        except Exception:
            self._log(provider, model, calls=1, input_chars=input_chars, output_chars=0, cache_hit=False)
            return None
        if not text:
            self._log(provider, model, calls=1, input_chars=input_chars, output_chars=0, cache_hit=False)
            return None
        try:
            self._store(cache_key, owner_user_id, provider, model, operation, text, int(settings["cache_ttl_seconds"]))
        except Exception as exc:
            log("llm.gateway_cache_store_failed", operation=operation, error=type(exc).__name__)
        self._log(provider, model, calls=1, input_chars=input_chars, output_chars=len(text), cache_hit=False)
        return text

    def can_generate(self, contact: dict[str, Any] | None = None) -> bool:
        settings = self._settings()
        return self._allowed(settings, contact) and bool(self._api_key(str(settings["provider"])))

    @staticmethod
    def cache_key(
        *,
        operation: str,
        provider: str,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        owner_user_id: int | None,
        cache_version: str,
    ) -> str:
        value = json.dumps(
            {
                "operation": operation,
                "provider": provider,
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "owner_user_id": owner_user_id,
                "cache_version": cache_version,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _settings(self) -> dict[str, Any]:
        llm = self.config.raw.get("llm", {}) if isinstance(self.config.raw.get("llm", {}), dict) else {}
        gateway = llm.get("gateway", {}) if isinstance(llm.get("gateway", {}), dict) else {}
        mode = str(gateway.get("mode", "high_value")).lower()
        return {
            "provider": llm.get("provider", "deepseek"),
            "model": llm.get("model", "deepseek-chat"),
            "base_url": llm.get("base_url") or ("https://api.openai.com/v1" if llm.get("provider") == "openai" else "https://api.deepseek.com"),
            "mode": mode if mode in {"off", "high_value", "all"} else "high_value",
            "icp_threshold": int(gateway.get("icp_threshold", 70)),
            "daily_calls": int(gateway.get("daily_calls", 200)),
            "daily_input_chars": int(gateway.get("daily_input_chars", 200000)),
            "daily_output_chars": int(gateway.get("daily_output_chars", 100000)),
            "cache_ttl_seconds": int(gateway.get("cache_ttl_seconds", 86400)),
            "cache_version": str(gateway.get("cache_version", "1")),
        }

    def _allowed(self, settings: dict[str, Any], contact: dict[str, Any] | None) -> bool:
        if settings["mode"] == "off":
            return False
        if settings["mode"] == "all":
            return True
        assessment = (contact or {}).get("icp_assessment")
        score = assessment.get("score") if isinstance(assessment, dict) else None
        if score is None:
            score = (contact or {}).get("icp_fit_score", (contact or {}).get("lead_score", 0))
        try:
            return int(score or 0) >= settings["icp_threshold"]
        except (TypeError, ValueError):
            return False

    def _api_key(self, provider: str) -> str:
        apis = getattr(self.config, "apis", {}) or {}
        return str(apis.get(f"{provider}_key", "") or apis.get("openai_key", ""))

    def _cached(self, cache_key: str, owner_user_id: int | None) -> str | None:
        getter = getattr(self.repo, "get_llm_gateway_cache", None)
        value = getter(cache_key, owner_user_id) if getter else None
        return str(value) if value else None

    def _store(self, cache_key: str, owner_user_id: int | None, provider: str, model: str, operation: str, text: str, ttl_seconds: int) -> None:
        store = getattr(self.repo, "store_llm_gateway_cache", None)
        if store:
            store(cache_key, owner_user_id, provider, model, operation, text, max(1, ttl_seconds))

    def _reserve(self, provider: str, model: str, input_chars: int, output_chars: int, settings: dict[str, Any]) -> bool:
        reserve = getattr(self.repo, "reserve_llm_gateway_budget", None)
        if not reserve:
            return True
        return bool(reserve(provider, model, input_chars, output_chars, settings["daily_calls"], settings["daily_input_chars"], settings["daily_output_chars"]))

    def _request(self, provider: str, model: str, base_url: str, api_key: str, messages: list[dict[str, str]], max_tokens: int, temperature: float) -> str:
        base_url = base_url.rstrip("/")
        if provider == "openai":
            data = self.http.request("POST", f"{base_url or 'https://api.openai.com/v1'}/responses", headers={"Authorization": f"Bearer {api_key}"}, json_body={"model": model, "input": messages, "max_output_tokens": max_tokens, "temperature": temperature})
            return _response_text(data)
        data = self.http.request("POST", f"{base_url}/chat/completions", headers={"Authorization": f"Bearer {api_key}"}, json_body={"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature})
        return str(data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()

    @staticmethod
    def _log(provider: str, model: str, *, calls: int, input_chars: int, output_chars: int, cache_hit: bool) -> None:
        log("llm.gateway", provider=provider, model=model, usage={"calls": calls, "input_chars": input_chars, "output_chars": output_chars}, cache_hit=cache_hit)


def _response_text(data: dict[str, Any]) -> str:
    if data.get("output_text"):
        return str(data["output_text"]).strip()
    for item in data.get("output") or []:
        for content in item.get("content") or []:
            if content.get("type") == "output_text" and content.get("text"):
                return str(content["text"]).strip()
    return ""


def _owner_user_id(contact: dict[str, Any] | None) -> int | None:
    try:
        value = int((contact or {}).get("owner_user_id") or 0)
        return value or None
    except (TypeError, ValueError):
        return None
