from __future__ import annotations

import json
from typing import Any

from ..clients import LLMClient
from ..config import AppConfig
from ..customer_intelligence import build_customer_profile
from ..db import Repository
from ..logging_utils import log


class ProfileAgentService:
    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo

    def summarize(self, contact_id: int, *, use_llm: bool = True) -> dict[str, Any]:
        contact = self.repo.get_contact(contact_id)
        if not contact:
            raise ValueError("Contact not found")
        fallback = _fallback_profile_insights(contact)
        llm_cfg = self.config.raw.get("llm", {})
        provider = llm_cfg.get("provider", "deepseek")
        api_key = self.config.apis.get(f"{provider}_key", "") or self.config.apis.get("openai_key", "")
        if not use_llm or not api_key:
            self.repo.update_profile_summary(contact_id, fallback["summary"], fallback)
            return fallback
        prompt = (
            "You are an overseas B2B sales customer-insight agent for Vertu. "
            "Use only the provided fields. Do not invent facts, revenue, news, suppliers, meetings, or pain points. "
            "Return strict JSON only, no Markdown. Required fields: "
            "summary, persona, icp_fit_score, intent_level, buying_stage, interests, pain_points, objections, risks, next_action, why_now, "
            "pain_point_strategy, followup_plan. "
            "pain_point_strategy must be an object with suspected_pain, outreach_angle, message_hook, evidence_to_use, question_to_ask, avoid. "
            "followup_plan must be four objects for Day 1, Day 3, Day 7, Day 14 with day, trigger, goal, message. "
            "icp_fit_score is 0-100. intent_level is only high/medium/low/unknown. "
            "If evidence is weak, say it is a hypothesis and recommend enrichment. "
            f"Name: {contact.get('first_name')} {contact.get('last_name')}; "
            f"role: {contact.get('job_title')}; company: {contact.get('company_name')}; industry: {contact.get('industry')}; "
            f"location: {contact.get('location')}; email status: {contact.get('email_status')}; "
            f"outreach status: {contact.get('status')}; sent step: {contact.get('sequence_step')}; "
            f"lifecycle: {contact.get('lifecycle_stage')}; SABCD: {contact.get('sabcd_stage')}; disposition: {contact.get('disposition')}; "
            f"notes: {contact.get('notes')}; lost reason: {contact.get('lost_reason')}; "
            f"source context: {contact.get('source_context')}; social profiles: {contact.get('social_profiles')}."
        )
        try:
            insights = _profile_insights_via_llm(api_key, provider, llm_cfg, prompt) or fallback
        except Exception as exc:
            log("profile_agent.failed", contact_id=contact_id, error=str(exc))
            insights = fallback
        insights = _normalize_profile_insights(insights, fallback)
        self.repo.update_profile_summary(contact_id, insights["summary"], insights)
        return insights


def _profile_insights_via_llm(api_key: str, provider: str, llm_cfg: dict[str, Any], prompt: str) -> dict[str, Any]:
    client = LLMClient(
        api_key,
        provider=provider,
        base_url=llm_cfg.get("base_url", "https://api.deepseek.com"),
        model=llm_cfg.get("model", "deepseek-chat"),
    )
    data = client.http.request(
        "POST",
        f"{client.base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json_body={
            "model": client.model,
            "messages": [
                {
                    "role": "system",
                    "content": "Return strict JSON only. Summarize the customer from provided fields only and do not invent facts.",
                },
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 820,
            "temperature": 0.2,
        },
    )
    text = str(data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    return json.loads(text)


def _fallback_profile_insights(contact: dict[str, Any]) -> dict[str, Any]:
    return build_customer_profile(contact)


def _normalize_profile_insights(insights: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    result = {**fallback, **(insights or {})}
    result["summary"] = str(result.get("summary") or fallback["summary"])[:500]
    result["persona"] = str(result.get("persona") or fallback["persona"])[:200]
    try:
        result["icp_fit_score"] = max(0, min(100, int(result.get("icp_fit_score", fallback["icp_fit_score"]))))
    except Exception:
        result["icp_fit_score"] = fallback["icp_fit_score"]
    if result.get("intent_level") not in {"high", "medium", "low", "unknown"}:
        result["intent_level"] = fallback["intent_level"]
    for key in ("interests", "pain_points", "objections", "risks"):
        value = result.get(key)
        if not isinstance(value, list):
            value = []
        result[key] = [str(item)[:160] for item in value[:5] if item]
    result["pain_point_strategy"] = _normalize_strategy(
        result.get("pain_point_strategy"),
        fallback.get("pain_point_strategy") or {},
    )
    result["followup_plan"] = _normalize_followup_plan(
        result.get("followup_plan"),
        fallback.get("followup_plan") or [],
    )
    result["next_action"] = str(result.get("next_action") or fallback["next_action"])[:300]
    result["why_now"] = str(result.get("why_now") or fallback["why_now"])[:300]
    result["buying_stage"] = str(result.get("buying_stage") or fallback["buying_stage"])[:80]
    return result


def _normalize_strategy(value: Any, fallback: dict[str, Any]) -> dict[str, str]:
    strategy = value if isinstance(value, dict) else fallback
    return {
        "suspected_pain": str(strategy.get("suspected_pain") or fallback.get("suspected_pain") or "")[:300],
        "outreach_angle": str(strategy.get("outreach_angle") or fallback.get("outreach_angle") or "")[:220],
        "message_hook": str(strategy.get("message_hook") or fallback.get("message_hook") or "")[:300],
        "evidence_to_use": str(strategy.get("evidence_to_use") or fallback.get("evidence_to_use") or "")[:300],
        "question_to_ask": str(strategy.get("question_to_ask") or fallback.get("question_to_ask") or "")[:220],
        "avoid": str(strategy.get("avoid") or fallback.get("avoid") or "")[:220],
    }


def _normalize_followup_plan(value: Any, fallback: list[dict[str, Any]]) -> list[dict[str, str]]:
    plan = value if isinstance(value, list) else fallback
    normalized = []
    for item in plan[:4]:
        if isinstance(item, dict):
            normalized.append({
                "day": str(item.get("day") or "")[:30],
                "trigger": str(item.get("trigger") or "")[:80],
                "goal": str(item.get("goal") or "")[:120],
                "message": str(item.get("message") or "")[:220],
            })
    return normalized


class StageAgentService:
    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo

    def analyze(
        self,
        contact_id: int,
        activity_id: int | None = None,
        content: str | None = None,
        stage: str | None = None,
        activity_type: str | None = None,
    ) -> dict[str, Any]:
        contact = self.repo.get_contact(contact_id)
        if not contact:
            raise ValueError("Contact not found")
        activities = self.repo.list_lifecycle_activities(contact_id, limit=8)
        target = next((item for item in activities if int(item["id"]) == int(activity_id or 0)), None)
        content = content or (target or {}).get("content") or ""
        stage = stage or (target or {}).get("lifecycle_stage") or contact.get("lifecycle_stage") or "lead"
        activity_type = activity_type or (target or {}).get("activity_type") or _default_activity_type(stage)
        fallback = _fallback_stage_analysis(contact, stage, activity_type, content)
        llm_cfg = self.config.raw.get("llm", {})
        provider = llm_cfg.get("provider", "deepseek")
        api_key = self.config.apis.get(f"{provider}_key", "") or self.config.apis.get("openai_key", "")
        if not api_key:
            return fallback
        try:
            analysis = _stage_analysis_via_llm(api_key, provider, llm_cfg, contact, activities, stage, activity_type, content) or fallback
            analysis = _normalize_stage_analysis(analysis, fallback)
        except Exception as exc:
            log("stage_agent.failed", contact_id=contact_id, activity_id=activity_id, error=str(exc))
            analysis = fallback
        if activity_id:
            self.repo.update_lifecycle_activity_analysis(activity_id, analysis)
        return analysis


def _stage_analysis_via_llm(
    api_key: str,
    provider: str,
    llm_cfg: dict[str, Any],
    contact: dict[str, Any],
    activities: list[dict[str, Any]],
    stage: str,
    activity_type: str,
    content: str,
) -> dict[str, Any]:
    client = LLMClient(
        api_key,
        provider=provider,
        base_url=llm_cfg.get("base_url", "https://api.deepseek.com"),
        model=llm_cfg.get("model", "deepseek-chat"),
    )
    history = "\n".join(
        f"- [{item.get('lifecycle_stage')}/{item.get('activity_type')}] {item.get('content')}"
        for item in activities[:5]
        if item.get("content")
    )
    prompt = (
        "You are a sales lifecycle analysis agent for Vertu overseas channel development. "
        "Use only provided facts and return strict JSON with: summary, intent, risks, missing_info, next_steps, suggested_stage, materials_to_prepare. "
        "intent must be high/medium/low/unknown. Arrays must contain short practical actions. "
        "For replied: analyze intent and next reply. For conversation: collect missing account facts. "
        "For meeting: suggest next meeting preparation. For business_plan: analyze landing likelihood and preparation materials. "
        "For trial_order: list country/logistics/compliance/order risks. For agency_agreement: list contract risk points. "
        "For store_creation: suggest store type, display, inventory, and opening preparation. "
        f"Contact: {contact.get('first_name')} {contact.get('last_name')}; company: {contact.get('company_name')}; role: {contact.get('job_title')}; "
        f"location: {contact.get('location')}; industry: {contact.get('industry')}; stage: {stage}; activity type: {activity_type}. "
        f"Current content: {content}\nHistory:\n{history}"
    )
    data = client.http.request(
        "POST",
        f"{client.base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json_body={
            "model": client.model,
            "messages": [
                {"role": "system", "content": "Return strict JSON only. You analyze sales lifecycle stages from provided facts only."},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 720,
            "temperature": 0.2,
        },
    )
    text = str(data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    return json.loads(text)


def _fallback_stage_analysis(contact: dict[str, Any], stage: str, activity_type: str, content: str) -> dict[str, Any]:
    label = {
        "lead": "lead",
        "replied": "replied",
        "conversation": "initial conversation",
        "meeting": "meeting",
        "business_plan": "business plan",
        "trial_order": "trial order",
        "agency_agreement": "agency agreement",
        "store_creation": "store creation",
    }.get(stage, stage)
    lower_content = content.lower()
    intent = "unknown"
    if any(token in lower_content for token in ("interested", "price", "proposal", "call", "meeting", "合作", "报价", "资料")):
        intent = "medium"
    if any(token in lower_content for token in ("order", "contract", "agreement", "visit", "buy", "订单", "合同", "代理")):
        intent = "high"
    return {
        "summary": f"Current stage is {label}. Record type: {activity_type}. Use the note to decide the next sales action.",
        "intent": intent,
        "risks": [] if content else ["Stage note is empty, so the analysis is only a default checklist."],
        "missing_info": _missing_info_for_stage(stage),
        "next_steps": _next_steps_for_stage(stage),
        "next_step": " / ".join(_next_steps_for_stage(stage)[:3]),
        "suggested_stage": stage,
        "materials_to_prepare": _materials_for_stage(stage),
        "prep_materials": _materials_for_stage(stage),
    }


def _normalize_stage_analysis(analysis: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    result = {**fallback, **(analysis or {})}
    result["summary"] = str(result.get("summary") or fallback["summary"])[:600]
    if result.get("intent") not in {"high", "medium", "low", "unknown"}:
        result["intent"] = fallback["intent"]
    for key in ("risks", "missing_info", "next_steps", "materials_to_prepare"):
        value = result.get(key)
        if not isinstance(value, list):
            value = []
        result[key] = [str(item)[:160] for item in value[:8] if item]
    result["next_step"] = " / ".join(result["next_steps"][:3])
    result["prep_materials"] = result["materials_to_prepare"]
    result["suggested_stage"] = str(result.get("suggested_stage") or fallback["suggested_stage"])[:80]
    return result


def _missing_info_for_stage(stage: str) -> list[str]:
    return {
        "replied": ["customer country/market", "channel type", "decision maker", "first order scale"],
        "conversation": ["company background", "channel capability", "budget", "store count", "decision chain"],
        "meeting": ["meeting objective", "attendee roles", "customer concerns", "next meeting time"],
        "business_plan": ["target country", "landing channel", "budget", "sales target", "timeline"],
        "trial_order": ["SKU", "quantity", "amount", "destination country", "logistics method", "label requirements"],
        "agency_agreement": ["agency territory", "exclusivity request", "payment cycle", "after-sales responsibility", "inventory obligation"],
        "store_creation": ["city/business district", "store size", "fit-out budget", "staffing", "inventory plan"],
    }.get(stage, ["country/region", "budget", "channel capability", "decision maker", "timeline"])


def _next_steps_for_stage(stage: str) -> list[str]:
    return {
        "replied": ["judge reply intent", "prepare three qualification questions", "schedule the next conversation"],
        "conversation": ["complete account background", "record missing customer facts", "confirm decision process"],
        "meeting": ["summarize meeting notes", "confirm next meeting topic", "prepare customer-specific materials"],
        "business_plan": ["prepare landing model", "estimate channel economics", "confirm budget and rollout target"],
        "trial_order": ["confirm SKU and quantity", "check compliance/logistics risk", "prepare trial order quote"],
        "agency_agreement": ["extract requested changes", "mark exclusivity/payment/after-sales/inventory risk points"],
        "store_creation": ["collect store facts", "prepare OA display suggestions", "suggest inventory and product mix"],
    }.get(stage, ["add a stage note", "generate the next follow-up task"])


def _materials_for_stage(stage: str) -> list[str]:
    return {
        "replied": ["channel cooperation overview", "brand/product introduction", "first order policy", "qualification questions"],
        "conversation": ["account research sheet", "channel capability checklist", "country/store information template"],
        "meeting": ["meeting agenda", "product materials", "cooperation policy", "post-meeting action list"],
        "business_plan": ["business plan template", "channel forecast sheet", "market entry notes", "next discussion questions"],
        "trial_order": ["SKU list", "trial order quote", "logistics proposal", "safety/tariff/label checklist"],
        "agency_agreement": ["agreement draft", "requested change list", "contract risk checklist"],
        "store_creation": ["OA display standard", "inventory structure proposal", "product mix suggestion", "opening checklist"],
    }.get(stage, ["account background", "product materials", "next-step question list"])


def _default_activity_type(stage: str) -> str:
    return {
        "replied": "reply",
        "conversation": "research",
        "meeting": "meeting_note",
        "business_plan": "business_plan",
        "trial_order": "trial_order",
        "agency_agreement": "agreement_review",
        "store_creation": "store_plan",
    }.get(stage, "note")


__all__ = ["ProfileAgentService", "StageAgentService"]
