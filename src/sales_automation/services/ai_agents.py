from __future__ import annotations

import json
from typing import Any

from ..clients import LLMClient
from ..config import AppConfig
from ..db import Repository
from ..logging_utils import log


class ProfileAgentService:
    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo

    def summarize(self, contact_id: int) -> dict[str, Any]:
        contact = self.repo.get_contact(contact_id)
        if not contact:
            raise ValueError("Contact not found")
        fallback = _fallback_profile_insights(contact)
        llm_cfg = self.config.raw.get("llm", {})
        provider = llm_cfg.get("provider", "deepseek")
        api_key = self.config.apis.get(f"{provider}_key", "") or self.config.apis.get("openai_key", "")
        if not api_key:
            self.repo.update_profile_summary(contact_id, fallback["summary"], fallback)
            return fallback
        prompt = (
            "你是海外销售运营画像 Agent。只根据已提供字段做结构化客户画像，不得编造事实。"
            "请只输出 JSON，不要 Markdown。字段必须包括："
            "summary, persona, icp_fit_score, intent_level, buying_stage, interests, pain_points, objections, risks, next_action, why_now。"
            "icp_fit_score 是 0-100 整数；intent_level 只能是 high/medium/low/unknown；"
            "interests/pain_points/objections/risks 是字符串数组；没有证据就用空数组。"
            f"姓名：{contact.get('first_name')} {contact.get('last_name')}；"
            f"职位：{contact.get('job_title')}；公司：{contact.get('company_name')}；行业：{contact.get('industry')}；"
            f"地区：{contact.get('location')}；邮箱状态：{contact.get('email_status')}；"
            f"外联状态：{contact.get('status')}，第 {contact.get('sequence_step')} 步；"
            f"已发送：{contact.get('sequence_step')}；生命周期：{contact.get('lifecycle_stage')}；"
            f"生命周期：{contact.get('lifecycle_stage')}；结论：{contact.get('disposition')}；"
            f"打开次数未知时不要推断；备注：{contact.get('notes')}；流失原因：{contact.get('lost_reason')}；"
            f"社媒：{contact.get('social_profiles')}。"
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
                {"role": "system", "content": "你只输出严格 JSON。你只根据已提供字段总结客户画像，不编造事实。"},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 520,
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
    name = " ".join(str(contact.get(key) or "") for key in ("first_name", "last_name")).strip() or "该客户"
    company = contact.get("company_name") or "未知公司"
    role = contact.get("job_title") or "未知职位"
    stage = contact.get("lifecycle_stage") or "lead"
    sent = int(contact.get("sequence_step") or 0)
    status = contact.get("status") or "new"
    score = 45
    if contact.get("email_status") == "valid":
        score += 15
    if str(contact.get("linkedin_url") or "").startswith("http"):
        score += 10
    if stage not in {"lead", "abandoned"}:
        score += 15
    if status in {"bounced", "unsubscribed"}:
        score = 0
    score = max(0, min(100, score))
    return {
        "summary": f"{name} 是 {company} 的 {role}，当前生命周期阶段为 {stage}。已完成第 {sent} 次外联，建议结合打开/回复情况决定继续跟进、等待或放弃。",
        "persona": f"{company} / {role}",
        "icp_fit_score": score,
        "intent_level": "unknown" if stage == "lead" else "medium",
        "buying_stage": stage,
        "interests": [],
        "pain_points": [],
        "objections": [],
        "risks": ["暂无足够互动信号"] if status not in {"replied", "sent_1", "sent_2", "sent_3"} else [],
        "next_action": "继续按照外联节奏触达；若第三次后仍未回复，进入等待池或放弃。",
        "why_now": "当前仅基于职位、公司、邮箱和外联状态判断。",
    }


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
        result[key] = [str(item)[:120] for item in value[:5] if item]
    result["next_action"] = str(result.get("next_action") or fallback["next_action"])[:300]
    result["why_now"] = str(result.get("why_now") or fallback["why_now"])[:300]
    result["buying_stage"] = str(result.get("buying_stage") or fallback["buying_stage"])[:80]
    return result


class StageAgentService:
    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo

    def analyze(self, contact_id: int, activity_id: int | None = None, content: str | None = None, stage: str | None = None, activity_type: str | None = None) -> dict[str, Any]:
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
            analysis = fallback
        else:
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
        "你是海外销售生命周期阶段 Agent。只根据提供信息分析，不编造事实。"
        "请只输出 JSON，不要 Markdown。字段必须包括：summary, intent, risks, missing_info, next_steps, suggested_stage, materials_to_prepare。"
        "intent 只能是 high/medium/low/unknown；risks/missing_info/next_steps/materials_to_prepare 都是字符串数组。"
        f"客户：{contact.get('first_name')} {contact.get('last_name')}；公司：{contact.get('company_name')}；职位：{contact.get('job_title')}；"
        f"国家/地区：{contact.get('location')}；行业：{contact.get('industry')}；当前阶段：{stage}；记录类型：{activity_type}。"
        f"本次记录：{content}\n历史记录：{history}"
        "阶段要求：回复阶段分析回复意图和下一步；初步沟通列缺失资料；约会分析会议下一步；"
        "商业计划分析落地可能性和准备资料；试订单按国家分析安全、关税、标签、物流风险；"
        "代理协议分析条款风险；门店创建分析客户类型、陈列、库存、产品占比和开店准备。"
    )
    data = client.http.request(
        "POST",
        f"{client.base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json_body={
            "model": client.model,
            "messages": [
                {"role": "system", "content": "你只输出严格 JSON。你是销售生命周期阶段分析 Agent。"},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 620,
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
        "lead": "线索",
        "replied": "回复",
        "conversation": "初步沟通",
        "meeting": "约会/会议",
        "business_plan": "商业计划",
        "trial_order": "试订单",
        "agency_agreement": "代理协议",
        "store_creation": "门店创建",
    }.get(stage, stage)
    lower_content = content.lower()
    intent = "unknown"
    if any(token in lower_content for token in ("可以", "了解", "资料", "政策", "报价", "合作", "meeting", "call", "interested")):
        intent = "medium"
    if any(token in lower_content for token in ("下单", "订单", "签", "代理", "合同", "visit", "buy")):
        intent = "high"
    next_steps = {
        "replied": ["判断客户意图", "准备首次沟通问题", "约定下一次沟通时间"],
        "conversation": ["补齐公司背景、渠道、预算、国家和门店信息", "整理客户缺失资料清单"],
        "meeting": ["沉淀会议纪要", "确认下一次会议议题", "准备客户关注材料"],
        "business_plan": ["准备落地模型", "整理对方国家和渠道资料", "确认预算与销量目标"],
        "trial_order": ["整理 SKU、数量、金额和物流方式", "检查安全、关税、标签和物流风险"],
        "agency_agreement": ["提取客户修改点", "标记独家、付款、售后、库存等风险条款"],
        "store_creation": ["收集门店面积、城市、商圈、预算和库存计划", "准备陈列和首批备货建议"],
    }.get(stage, ["补充阶段记录", "生成下一步跟进任务"])
    materials = {
        "replied": ["海外渠道合作资料", "首批订单政策", "品牌/产品介绍", "下一步沟通问题清单"],
        "conversation": ["客户背景调研表", "渠道能力问题清单", "国家/城市/门店信息模板"],
        "meeting": ["会议议程", "产品资料", "报价或合作政策", "会议后待办清单"],
        "business_plan": ["商业计划模板", "渠道测算表", "市场进入资料", "下次沟通问题"],
        "trial_order": ["SKU 清单", "试订单报价", "物流方案", "安全/关税/标签检查清单"],
        "agency_agreement": ["协议版本", "客户修改点清单", "付款/独家/售后/库存条款风险表"],
        "store_creation": ["OA 标准陈列", "库存结构建议", "产品占比建议", "开业准备清单"],
    }.get(stage, ["客户背景资料", "产品资料", "下一步沟通问题清单"])
    missing = {
        "replied": ["客户国家/市场", "渠道类型", "预计首批订单规模", "是否决策人"],
        "conversation": ["公司背景", "渠道能力", "预算", "门店数量", "决策链"],
        "meeting": ["会议目标", "参会人角色", "客户关注点", "下一次会议时间"],
        "business_plan": ["目标国家", "落地渠道", "预算", "销量目标", "时间表"],
        "trial_order": ["SKU", "数量", "金额", "目的国", "物流方式", "标签要求"],
        "agency_agreement": ["代理区域", "独家要求", "付款周期", "售后责任", "库存义务"],
        "store_creation": ["城市/商圈", "门店面积", "装修预算", "人员配置", "库存计划"],
    }.get(stage, ["国家/地区", "预算", "渠道能力", "决策人", "时间计划"])
    return {
        "summary": f"当前处于{label}阶段，已记录 {activity_type} 内容。需要基于更多客户资料继续判断推进路径。",
        "intent": intent,
        "risks": ["信息不足，暂不能做强判断"] if not content else [],
        "missing_info": missing,
        "next_steps": next_steps,
        "suggested_stage": stage,
        "materials_to_prepare": materials,
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
        result[key] = [str(item)[:140] for item in value[:8] if item]
    result["suggested_stage"] = str(result.get("suggested_stage") or fallback["suggested_stage"])[:80]
    return result


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
