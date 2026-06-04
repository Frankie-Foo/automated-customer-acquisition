from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .clients import HunterClient, LLMClient, MailClient, NinjaPearClient, PeopleDataLabsClient, PeopleDBClient, ProspeoClient, ProxycurlClient, SlackClient, is_full_email
from .config import AppConfig
from .db import Repository
from .logging_utils import log
from .rendering import open_pixel_url, render_string, render_template, unsubscribe_url


class SourcingService:
    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo

    def source(self, criteria: dict[str, Any], limit: int) -> tuple[int, int]:
        provider = self.config.raw.get("sourcing", {}).get("provider", "prospeo")
        prospeo_key = self.config.apis.get("prospeo_key", "")
        ninjapear_key = self.config.apis.get("ninjapear_key", "")
        if provider == "prospeo" and prospeo_key:
            role = criteria.get("role") or criteria.get("title")
            if not role:
                raise RuntimeError("Prospeo sourcing requires role")
            try:
                contacts = ProspeoClient(prospeo_key).search_people(
                    company_website=criteria.get("company_website"),
                    role=role,
                    industry=criteria.get("industry"),
                    location=criteria.get("location"),
                    limit=limit,
                )
            except RuntimeError as exc:
                if "NO_RESULTS" in str(exc):
                    contacts = []
                else:
                    raise
        elif ninjapear_key:
            company_website = criteria.get("company_website") or criteria.get("company")
            role = criteria.get("role") or criteria.get("title")
            if not company_website or not role:
                raise RuntimeError("NinjaPear sourcing requires company_website and role")
            contacts = NinjaPearClient(ninjapear_key).search_employees(
                company_website=company_website,
                role=role,
                location=criteria.get("location"),
                limit=limit,
            )
        else:
            key = self.config.apis.get("proxycurl_key", "")
            if not key:
                raise RuntimeError("Missing apis.prospeo_key or apis.ninjapear_key")
            contacts = ProxycurlClient(key).search_people(criteria, limit)
        contacts = [c for c in contacts if c.get("linkedin_url")]
        inserted, skipped = self.repo.upsert_contacts(contacts)
        log("source.completed", inserted=inserted, skipped=skipped)
        return inserted, skipped


class EnrichmentService:
    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo

    def enrich(self, limit: int) -> tuple[int, int]:
        hunter_key = self.config.apis.get("hunter_key", "")
        ninjapear_key = self.config.apis.get("ninjapear_key", "")
        prospeo_key = self.config.apis.get("prospeo_key", "")
        proxycurl_key = self.config.apis.get("proxycurl_key", "")
        if not hunter_key and not ninjapear_key and not prospeo_key:
            raise RuntimeError("Missing apis.hunter_key, apis.prospeo_key, or apis.ninjapear_key")
        hunter = HunterClient(hunter_key) if hunter_key else None
        ninjapear = NinjaPearClient(ninjapear_key) if ninjapear_key else None
        prospeo = ProspeoClient(prospeo_key) if prospeo_key else None
        proxycurl = ProxycurlClient(proxycurl_key) if proxycurl_key else None
        ok = failed = 0
        for contact in self.repo.list_for_enrichment(limit):
            try:
                fields = self._enrich_one(contact, hunter, proxycurl, ninjapear, prospeo)
                note = None if fields.get("email_status") == "valid" else "No verified email found"
                self.repo.update_enrichment(contact["id"], fields, error=note)
                ok += 1
            except Exception as exc:
                self.repo.update_enrichment(contact["id"], {"email_status": "unknown"}, error=str(exc))
                failed += 1
                log("enrich.failed", contact_id=contact["id"], error=str(exc))
        log("enrich.completed", ok=ok, failed=failed)
        return ok, failed

    def _enrich_one(self, contact: dict[str, Any], hunter: HunterClient | None, proxycurl: ProxycurlClient | None, ninjapear: NinjaPearClient | None, prospeo: ProspeoClient | None) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        domain = contact.get("company_domain")
        if domain and is_full_email(contact.get("email")):
            fields["email"] = contact.get("email")
            fields["email_status"] = contact.get("email_status") or "valid"
        elif domain and ninjapear:
            found = ninjapear.find_work_email(domain=domain, first_name=contact.get("first_name"), last_name=contact.get("last_name"))
            email = found.get("work_email") or found.get("email")
            if email:
                fields["email"] = email
                fields["email_status"] = "valid"
        elif prospeo:
            try:
                found = prospeo.enrich_person(contact)
            except RuntimeError as exc:
                if "NO_MATCH" in str(exc):
                    return {"email_status": "unknown"}
                raise
            email_obj = found.get("email") or found.get("work_email")
            email = email_obj.get("email") if isinstance(email_obj, dict) else email_obj
            if is_full_email(email):
                fields["email"] = email
                fields["email_status"] = "valid"
        elif domain and hunter:
            found = hunter.find_email(domain, contact.get("first_name"), contact.get("last_name"))
            email = found.get("email")
            if email:
                verified = hunter.verify_email(email)
                fields["email"] = email
                fields["email_status"] = verified.get("status", "unknown")
        if proxycurl and domain:
            company = proxycurl.company_lookup(domain)
            fields["company_size"] = company.get("company_size") or company.get("employee_count")
            fields["company_funding"] = company.get("funding_data") or company.get("funding_stage")
            fields["industry"] = company.get("industry") or contact.get("industry")
        return fields or {"email_status": "unknown"}


class SocialEnrichmentService:
    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo

    def enrich(self, limit: int) -> tuple[int, int]:
        peopledb_key = self.config.apis.get("peopledb_key", "")
        pdl_key = self.config.apis.get("pdl_key", "")
        if peopledb_key:
            client: Any = PeopleDBClient(peopledb_key)
            provider = "peopledb"
        elif pdl_key:
            client = PeopleDataLabsClient(pdl_key)
            provider = "pdl"
        else:
            raise RuntimeError("Missing apis.peopledb_key or apis.pdl_key")
        ok = failed = 0
        for contact in self.repo.list_for_social_enrichment(limit):
            try:
                record = client.enrich_person(contact)
                profiles = _extract_social_profiles(record, contact)
                if not profiles:
                    self.repo.update_social_profiles(contact["id"], {}, error="No social profiles found")
                    failed += 1
                else:
                    self.repo.update_social_profiles(contact["id"], profiles)
                    ok += 1
            except Exception as exc:
                self.repo.update_social_profiles(contact["id"], {}, error=str(exc))
                failed += 1
                log("social_enrich.failed", provider=provider, contact_id=contact["id"], error=str(exc))
        log("social_enrich.completed", provider=provider, ok=ok, failed=failed)
        return ok, failed


def _extract_social_profiles(record: dict[str, Any], contact: dict[str, Any] | None = None) -> dict[str, Any]:
    profiles: dict[str, Any] = {}
    direct_fields = {
        "linkedin": ("linkedin_url", "linkedin"),
        "twitter": ("twitter_url", "twitter"),
        "github": ("github_url", "github"),
        "facebook": ("facebook_url", "facebook"),
        "website": ("personal_website", "website"),
    }
    for target, keys in direct_fields.items():
        value = _first_string(record, *keys)
        if value:
            profiles[target] = value
    if contact and str(contact.get("linkedin_url") or "").startswith("http"):
        profiles.setdefault("linkedin", contact["linkedin_url"])
    github_login = _first_string(record, "github_login", "github_username")
    if github_login:
        profiles.setdefault("github", f"https://github.com/{github_login}")
    linkedin_id = _first_string(record, "linkedin_public_identifier", "linkedin_id")
    if linkedin_id and not str(linkedin_id).startswith("http"):
        profiles.setdefault("linkedin", f"https://www.linkedin.com/in/{linkedin_id}")
    twitter_login = _first_string(record, "twitter_login", "twitter_username", "x_login")
    if twitter_login and not str(twitter_login).startswith("http"):
        profiles.setdefault("twitter", f"https://x.com/{twitter_login.lstrip('@')}")
    for item in record.get("profiles") or []:
        if not isinstance(item, dict):
            continue
        network = str(item.get("network") or item.get("type") or "").lower()
        url = item.get("url") or item.get("profile_url") or item.get("id")
        if not url:
            continue
        if "linkedin" in network:
            profiles.setdefault("linkedin", url)
        elif network in {"twitter", "x"} or "twitter" in network:
            profiles.setdefault("twitter", url)
        elif "github" in network:
            profiles.setdefault("github", url)
        elif "facebook" in network:
            profiles.setdefault("facebook", url)
    return {key: value for key, value in profiles.items() if value}


def _first_string(record: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return None


class QueueService:
    def __init__(self, repo: Repository):
        self.repo = repo

    def queue(self, limit: int) -> int:
        count = self.repo.queue_contacts(limit)
        log("queue.completed", count=count)
        return count


class LifecycleService:
    STAGES = [
        "lead", "replied", "conversation", "meeting", "business_plan",
        "store_visit", "trial_order", "agency_agreement", "hq_visit",
        "signed", "maintenance", "waiting_pool", "abandoned",
    ]

    def __init__(self, repo: Repository):
        self.repo = repo

    def update(
        self,
        contact_id: int,
        *,
        lifecycle_stage: str | None = None,
        disposition: str | None = None,
        next_action_at: str | None = None,
        notes: str | None = None,
        lost_reason: str | None = None,
        owner: str | None = None,
    ) -> dict[str, Any]:
        if lifecycle_stage and lifecycle_stage not in self.STAGES:
            raise ValueError(f"Unsupported lifecycle_stage: {lifecycle_stage}")
        if disposition and disposition not in {"active", "waiting", "abandoned", "won", "lost"}:
            raise ValueError(f"Unsupported disposition: {disposition}")
        if lifecycle_stage == "abandoned" and not disposition:
            disposition = "abandoned"
        if lifecycle_stage == "signed" and not disposition:
            disposition = "won"
        self.repo.update_lifecycle(
            contact_id,
            lifecycle_stage=lifecycle_stage,
            disposition=disposition,
            next_action_at=next_action_at,
            notes=notes,
            lost_reason=lost_reason,
            owner=owner,
        )
        return {"contact_id": contact_id, "lifecycle_stage": lifecycle_stage, "disposition": disposition}


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


class PersonalizedEmailService:
    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo

    def draft(self, contact_id: int, *, mode: str = "ai", custom_subject: str | None = None, custom_body: str | None = None) -> dict[str, str]:
        contact = self.repo.get_contact(contact_id)
        if not contact:
            raise ValueError("Contact not found")
        if mode == "custom":
            return {
                "subject": custom_subject or f"Quick question about {contact.get('company_name') or 'your business'}",
                "body": custom_body or "",
            }
        return self._ai_draft(contact)

    def send(self, contact_id: int, *, subject: str, body: str, mode: str = "custom") -> dict[str, Any]:
        contact = self.repo.get_contact(contact_id)
        if not contact:
            raise ValueError("Contact not found")
        if not is_full_email(contact.get("email")):
            raise ValueError("Contact does not have a valid email")
        sender = self.config.sender
        api_key = self.config.apis.get(f"{sender.get('provider', 'resend')}_key", "")
        mailer = MailClient(sender.get("provider", "resend"), api_key, sender)
        base_url = self.config.raw.get("app", {}).get("public_base_url", "http://127.0.0.1:8765")
        step = int(contact.get("sequence_step") or 0) + 1
        values = {
            **contact,
            "sender_name": sender.get("name", ""),
            "unsubscribe_url": unsubscribe_url(contact, base_url),
        }
        text = render_string(body, values)
        if "{{unsubscribe_url}}" not in body:
            text = f"{text.rstrip()}\n\nUnsubscribe: {values['unsubscribe_url']}"
        html_body = "<br>".join(_html_escape(line) for line in text.splitlines())
        html_body += f'<img src="{open_pixel_url(contact, step, base_url)}" width="1" height="1" alt="" />'
        message_id = mailer.send(
            contact["email"],
            subject,
            html_body,
            text,
            metadata={"contact_id": contact["id"], "sequence_step": step, "mode": mode},
        )
        recorded = self.repo.record_manual_sent(contact["id"], step, subject, message_id, {"dry_run": sender.get("dry_run", True), "mode": mode})
        return {"sent": bool(recorded), "contact_id": contact_id, "step": step, "message_id": message_id}

    def _ai_draft(self, contact: dict[str, Any]) -> dict[str, str]:
        fallback = self._fallback_draft(contact)
        llm_cfg = self.config.raw.get("llm", {})
        provider = llm_cfg.get("provider", "deepseek")
        api_key = self.config.apis.get(f"{provider}_key", "") or self.config.apis.get("openai_key", "")
        if not api_key:
            return fallback
        insights = contact.get("profile_insights") if isinstance(contact.get("profile_insights"), dict) else {}
        activities = self.repo.list_lifecycle_activities(int(contact["id"]), limit=5)
        history = "\n".join(f"- {item.get('content')}" for item in activities if item.get("content"))
        prompt = (
            "你是海外B2B销售邮件助手。只根据客户资料生成一封简洁英文邮件，不编造事实。"
            "请只输出 JSON，不要 Markdown。字段：subject, body。body 使用纯文本，80-140词，语气自然。"
            "必须包含明确但轻量的下一步请求，不要夸大，不要承诺不存在的案例。"
            f"客户：{contact.get('first_name')} {contact.get('last_name')}；职位：{contact.get('job_title')}；"
            f"公司：{contact.get('company_name')}；行业：{contact.get('industry')}；地区：{contact.get('location')}；"
            f"生命周期：{contact.get('lifecycle_stage')}；画像：{insights}；历史记录：{history}；"
            f"发件人：{self.config.sender.get('name')}。"
        )
        try:
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
                        {"role": "system", "content": "You only output strict JSON for a B2B sales email draft."},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 420,
                    "temperature": 0.35,
                },
            )
            text = str(data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
            if text.startswith("```"):
                text = text.strip("`")
                if text.lower().startswith("json"):
                    text = text[4:].strip()
            draft = json.loads(text)
            subject = str(draft.get("subject") or fallback["subject"])[:160]
            body = str(draft.get("body") or fallback["body"])[:3000]
            return {"subject": subject, "body": body}
        except Exception as exc:
            log("email_draft.failed", contact_id=contact.get("id"), error=str(exc))
            return fallback

    def _fallback_draft(self, contact: dict[str, Any]) -> dict[str, str]:
        company = contact.get("company_name") or "your business"
        first = contact.get("first_name") or "there"
        sender = self.config.sender.get("name", "")
        subject = f"Quick question about {company}"
        body = (
            f"Hi {first},\n\n"
            f"I noticed your work as {contact.get('job_title') or 'a leader'} at {company} and thought this might be relevant.\n\n"
            "Would it make sense to have a short conversation about overseas channel cooperation and the next practical step?\n\n"
            f"Best,\n{sender}\n\n"
            "Unsubscribe: {{unsubscribe_url}}"
        )
        return {"subject": subject, "body": body}


def _html_escape(value: str) -> str:
    import html

    return html.escape(value)


class OutreachService:
    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo

    def send_due(self, limit: int) -> int:
        sender = self.config.sender
        daily_limit = int(sender.get("daily_limit", 100))
        remaining = max(0, daily_limit - self.repo.sent_today_count())
        if remaining == 0:
            log("send.skipped_daily_limit", daily_limit=daily_limit)
            return 0
        api_key = self.config.apis.get(f"{sender.get('provider', 'resend')}_key", "")
        mailer = MailClient(sender.get("provider", "resend"), api_key, sender)
        llm_cfg = self.config.raw.get("llm", {})
        provider = llm_cfg.get("provider", "deepseek")
        api_key = self.config.apis.get(f"{provider}_key", "") or self.config.apis.get("openai_key", "")
        ai = LLMClient(
            api_key,
            provider=provider,
            base_url=llm_cfg.get("base_url", "https://api.deepseek.com"),
            model=llm_cfg.get("model", "deepseek-chat"),
        )
        sent = 0
        for contact in self.repo.due_for_sending(min(limit, remaining)):
            step_cfg = self._next_step_config(contact)
            if not step_cfg or not self._step_due(contact, step_cfg):
                continue
            subject = render_string(step_cfg["subject"], contact)
            values = {
                **contact,
                "sender_name": sender.get("name", ""),
                "unsubscribe_url": unsubscribe_url(contact, self.config.raw.get("app", {}).get("public_base_url", "http://127.0.0.1:8765")),
                "ai_opener": ai.opener(contact) if step_cfg.get("ai_opener") else "",
            }
            template = self.config.root_dir / step_cfg["body_template"]
            text, html = render_template(template, values)
            base_url = self.config.raw.get("app", {}).get("public_base_url", "http://127.0.0.1:8765")
            html += f'<img src="{open_pixel_url(contact, int(step_cfg["step"]), base_url)}" width="1" height="1" alt="" />'
            message_id = mailer.send(contact["email"], subject, html, text, metadata={"contact_id": contact["id"], "sequence_step": step_cfg["step"]})
            if self.repo.record_sent(contact["id"], int(step_cfg["step"]), subject, message_id, {"dry_run": sender.get("dry_run", True)}):
                sent += 1
                log("send.sent", contact_id=contact["id"], step=step_cfg["step"], dry_run=sender.get("dry_run", True))
        log("send.completed", sent=sent)
        return sent

    def _next_step_config(self, contact: dict[str, Any]) -> dict[str, Any] | None:
        next_step = int(contact.get("sequence_step") or 0) + 1
        for step in self.config.sequence:
            if int(step["step"]) == next_step:
                return step
        return None

    def _step_due(self, contact: dict[str, Any], step: dict[str, Any]) -> bool:
        if contact.get("status") == "queued":
            return True
        last = contact.get("last_contacted_at")
        if not last:
            return True
        if isinstance(last, str):
            last = datetime.fromisoformat(last)
        return datetime.now(UTC) >= last + timedelta(days=int(step.get("delay_days", 0)))


class WebhookService:
    def __init__(self, repo: Repository, notifier: SlackClient | None = None):
        self.repo = repo
        self.notifier = notifier

    def process_payload(self, provider: str, payload: dict[str, Any]) -> str:
        contact_id = _extract_contact_id(payload)
        event_type = _extract_event_type(provider, payload)
        if not contact_id:
            raise ValueError("Webhook payload does not include contact_id metadata")
        self.repo.record_event(contact_id, event_type, payload)
        if event_type == "replied" and self.notifier:
            self.notifier.notify(f"Lead replied: contact #{contact_id}")
        log("webhook.processed", provider=provider, contact_id=contact_id, event_type=event_type)
        return event_type

    def process_file(self, provider: str, path: Path) -> str:
        return self.process_payload(provider, json.loads(path.read_text(encoding="utf-8")))


class SchedulerService:
    def __init__(self, config: AppConfig, repo: Repository):
        self.config = config
        self.repo = repo

    def run_once(self, enrich_limit: int, queue_limit: int, send_limit: int) -> None:
        with self.repo.db.connect() as conn:
            row = conn.execute("SELECT pg_try_advisory_lock(20260603) AS locked").fetchone()
            if not row["locked"]:
                log("scheduler.skipped_locked")
                return
            try:
                EnrichmentService(self.config, self.repo).enrich(enrich_limit)
                QueueService(self.repo).queue(queue_limit)
                OutreachService(self.config, self.repo).send_due(send_limit)
                log("scheduler.completed")
            finally:
                conn.execute("SELECT pg_advisory_unlock(20260603)")


def _extract_contact_id(payload: dict[str, Any]) -> int | None:
    for path in (
        ("contact_id",),
        ("data", "contact_id"),
        ("data", "metadata", "contact_id"),
        ("data", "tags", "contact_id"),
        ("metadata", "contact_id"),
        ("tags", "contact_id"),
    ):
        value: Any = payload
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if value:
            return int(value)
    tags = payload.get("tags") or payload.get("data", {}).get("tags")
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, dict) and tag.get("name") == "contact_id" and tag.get("value"):
                return int(tag["value"])
    return None


def _extract_event_type(provider: str, payload: dict[str, Any]) -> str:
    raw = str(payload.get("type") or payload.get("event") or payload.get("event_type") or "").lower()
    if "bounce" in raw:
        return "bounced"
    if "unsubscribe" in raw:
        return "unsubscribed"
    if "reply" in raw:
        return "replied"
    if "click" in raw:
        return "clicked"
    if "open" in raw:
        return "opened"
    return raw or "opened"
