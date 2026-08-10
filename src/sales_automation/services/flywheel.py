from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable

from ..outbound_quality import calibration_summary, summarize_experiment


REGION_ALIASES = {
    "middle_east": ("uae", "united arab emirates", "dubai", "saudi", "qatar", "kuwait", "bahrain", "oman", "iran", "iraq", "jordan", "israel", "阿联酋", "沙特", "卡塔尔", "科威特", "巴林", "阿曼", "伊朗", "伊拉克", "约旦", "以色列"),
    "cis": ("russia", "russian", "kazakhstan", "uzbekistan", "ukraine", "belarus", "armenia", "azerbaijan", "georgia", "kyrgyzstan", "tajikistan", "turkmenistan", "moldova", "俄罗斯", "哈萨克斯坦", "乌兹别克斯坦", "乌克兰", "白俄罗斯", "亚美尼亚", "阿塞拜疆", "格鲁吉亚", "吉尔吉斯斯坦", "塔吉克斯坦", "土库曼斯坦", "摩尔多瓦"),
    "south_asia": ("india", "pakistan", "bangladesh", "sri lanka", "nepal", "印度", "巴基斯坦", "孟加拉", "斯里兰卡", "尼泊尔"),
    "southeast_asia": ("singapore", "malaysia", "indonesia", "thailand", "vietnam", "philippines", "cambodia", "myanmar", "laos", "brunei", "新加坡", "马来西亚", "印度尼西亚", "印尼", "泰国", "越南", "菲律宾", "柬埔寨", "缅甸", "老挝", "文莱"),
    "europe": ("europe", "uk", "united kingdom", "germany", "france", "italy", "spain", "netherlands", "switzerland", "欧洲", "英国", "德国", "法国", "意大利", "西班牙", "荷兰", "瑞士"),
    "north_america": ("united states", "usa", "canada", "mexico", "美国", "加拿大", "墨西哥"),
}


def normalize_region(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "unknown"
    for region, aliases in REGION_ALIASES.items():
        if any(alias in text for alias in aliases):
            return region
    return text.replace(" ", "_")[:80] or "unknown"


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _contact_scope(row: dict[str, Any]) -> str:
    context = row.get("source_context") if isinstance(row.get("source_context"), dict) else {}
    return normalize_region(row.get("region") or row.get("country") or context.get("region") or context.get("country") or row.get("location"))


def build_flywheel_metrics(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    metrics = {
        "sample_contacts": len(rows),
        "sent": sum(_as_int(row.get("sent")) for row in rows),
        "delivered": sum(_as_int(row.get("delivered")) for row in rows),
        "opened": sum(_as_int(row.get("opened")) for row in rows),
        "replied": sum(_as_int(row.get("replied")) for row in rows),
        "positive_replies": sum(_as_int(row.get("positive_replies")) for row in rows),
        "negative_replies": sum(_as_int(row.get("negative_replies")) for row in rows),
        "bounced": sum(_as_int(row.get("bounced")) for row in rows),
        "unsubscribed": sum(_as_int(row.get("unsubscribed")) for row in rows),
        "meetings": sum(_as_int(row.get("meetings")) for row in rows),
        "won": sum(_as_int(row.get("won")) for row in rows),
        "lost": sum(_as_int(row.get("lost")) for row in rows),
    }
    sent = metrics["sent"]
    metrics.update({
        "delivery_rate": _rate(metrics["delivered"], sent),
        "open_rate": _rate(metrics["opened"], sent),
        "reply_rate": _rate(metrics["replied"], sent),
        "positive_reply_rate": _rate(metrics["positive_replies"], sent),
        "bounce_rate": _rate(metrics["bounced"], sent),
        "meeting_rate": _rate(metrics["meetings"], sent),
        "win_rate": _rate(metrics["won"], sent),
    })
    return metrics


def _segment_rows(rows: list[dict[str, Any]], key: str, min_sent: int = 3) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value = _contact_scope(row) if key == "region" else str(row.get(key) or "").strip()
        if value:
            groups[value].append(row)
    result = []
    for value, items in groups.items():
        metrics = build_flywheel_metrics(items)
        if metrics["sent"] >= min_sent:
            result.append({"value": value[:100], **metrics})
    return sorted(result, key=lambda item: (item["positive_reply_rate"], item["reply_rate"], item["sent"]), reverse=True)


def build_flywheel_strategy(rows: Iterable[dict[str, Any]], *, scope_type: str, scope_key: str, window_start: str, window_end: str, min_samples: int = 5) -> dict[str, Any]:
    rows = list(rows)
    metrics = build_flywheel_metrics(rows)
    sufficient = metrics["sent"] >= min_samples
    best_regions = _segment_rows(rows, "region")[:5]
    best_industries = _segment_rows(rows, "industry")[:5]
    best_roles = _segment_rows(rows, "job_title")[:5]
    guidance: list[str] = []
    if not sufficient:
        guidance.append(f"当前样本不足（已发送 {metrics['sent']}，至少需要 {min_samples}），暂不改变既有获客和话术策略。")
    else:
        if metrics["positive_replies"]:
            guidance.append("优先复制已经产生正向回复的地区、行业和职位组合，保留对应的价值角度。")
        if metrics["opened"] and not metrics["replied"]:
            guidance.append("有打开但没有回复的线索，下一次应更换具体价值点，不要重复原主题。")
        if metrics["reply_rate"] == 0:
            guidance.append("当前窗口没有回复证据，下一轮先缩小目标人群并人工抽查内容，再扩大触达量。")
        if metrics["bounce_rate"] > 0.05:
            guidance.append("退信率偏高，暂停高风险域名和未经验证的邮箱，优先使用已验证的个人工作邮箱。")
        if metrics["won"]:
            guidance.append("已成交客户的来源和画像应进入案例库，作为后续相似客户评分的正向样本。")
    preferred_regions = [item["value"] for item in best_regions if item["positive_replies"] > 0][:3]
    preferred_industries = [item["value"] for item in best_industries if item["positive_replies"] > 0][:3]
    preferred_roles = [item["value"] for item in best_roles if item["positive_replies"] > 0][:3]
    return {
        "scope_type": scope_type,
        "scope_key": scope_key,
        "status": "active" if sufficient else "insufficient_sample",
        "window_start": window_start,
        "window_end": window_end,
        "sample_size": sum(1 for row in rows if _as_int(row.get("sent")) > 0),
        "metrics": metrics,
        "rules": {"min_samples": min_samples, "preferred_regions": preferred_regions, "preferred_industries": preferred_industries, "preferred_roles": preferred_roles, "do_not_auto_expand": not sufficient},
        "guidance": {
            "prompt_guidance": " ".join(guidance),
            "next_cycle": guidance,
            "score_adjustment_max": 5 if sufficient else 0,
        },
        "evidence": [
            {"dimension": "region", "items": best_regions[:3]},
            {"dimension": "industry", "items": best_industries[:3]},
            {"dimension": "job_title", "items": best_roles[:3]},
        ],
    }


def build_icp_calibration_examples(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Infer only high-signal labels from observed outcomes.

    A neutral reply, an open, or a missing outcome is not a training label.
    This keeps automatic threshold changes conservative and explainable.
    """
    examples = []
    for row in rows:
        current_assessment = row.get("icp_assessment")
        assessment = (
            current_assessment.get("assessment_before_outcome")
            if isinstance(current_assessment, dict)
            and isinstance(current_assessment.get("assessment_before_outcome"), dict)
            else current_assessment
        )
        if not isinstance(assessment, dict) or "qualified" not in assessment:
            continue
        positive = bool(
            _as_int(row.get("positive_replies"))
            or _as_int(row.get("positive_outcomes"))
            or _as_int(row.get("meetings"))
            or _as_int(row.get("won"))
            or str(row.get("lifecycle_stage") or "") in {
                "meeting", "business_plan", "trial_order",
                "agency_agreement", "store_visit", "hq_visit", "signed", "maintenance",
            }
        )
        negative = bool(
            _as_int(row.get("negative_replies"))
            or _as_int(row.get("negative_outcomes"))
        )
        if positive == negative:
            continue
        examples.append({
            "contact_id": row.get("id"),
            "predicted_qualified": bool(assessment.get("qualified")),
            "expected_qualified": positive,
            "reason": "observed_positive_outcome" if positive else "observed_negative_outcome",
            "profile_version": assessment.get("profile_version"),
        })
    return examples


def build_learning_plan(
    rows: Iterable[dict[str, Any]],
    *,
    profile: dict[str, Any] | None = None,
    experiments: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Build bounded learning actions without mutating the repository."""
    profile = profile or {}
    current_threshold = int(profile.get("qualified_threshold") or 70)
    examples = build_icp_calibration_examples(rows)
    calibration = calibration_summary(examples, current_threshold=current_threshold)
    threshold_action = None
    if (
        calibration["reviewed"] >= 10
        and calibration["proposed_threshold"] != current_threshold
    ):
        threshold_action = {
            "action": "adjust_icp_threshold",
            "profile_id": profile.get("id"),
            "from": current_threshold,
            "to": max(40, min(90, int(calibration["proposed_threshold"]))),
            "reason": calibration["recommendation"],
        }

    experiment_actions = []
    for item in experiments:
        if not isinstance(item, dict) or item.get("status") != "active":
            continue
        analysis = item.get("analysis") if isinstance(item.get("analysis"), dict) else None
        if not analysis:
            measured = item.get("measured_variants") or item.get("variants") or []
            analysis = summarize_experiment(measured)
        winner = analysis.get("winner")
        if winner:
            experiment_actions.append({
                "action": "select_experiment_winner",
                "experiment_id": item.get("id"),
                "experiment_name": item.get("name"),
                "variant": str(winner),
                "reason": analysis.get("recommendation") or "Decision-grade positive-reply evidence.",
                "analysis": analysis,
            })
    return {
        "calibration": calibration,
        "calibration_examples": len(examples),
        "threshold_action": threshold_action,
        "experiment_actions": experiment_actions,
    }


class DataFlywheelService:
    """Aggregate outcomes into explainable, reusable strategy snapshots."""

    def __init__(self, config: Any, repo: Any):
        self.config = config
        self.repo = repo
        self._cache: dict[tuple[str, str], dict[str, Any] | None] = {}

    def run_once(self, *, window_days: int = 30, min_samples: int = 5) -> dict[str, Any]:
        if not hasattr(self.repo, "list_flywheel_contact_rows") or not hasattr(self.repo, "save_flywheel_snapshot"):
            return {"status": "disabled", "reason": "repository_not_ready", "snapshots": []}
        window_days = max(1, min(int(window_days), 365))
        min_samples = max(3, min(int(min_samples), 1000))
        rows = self.repo.list_flywheel_contact_rows(window_days=window_days)
        end = datetime.now(UTC)
        start = end - timedelta(days=window_days)
        scopes: dict[tuple[str, str], list[dict[str, Any]]] = {("global", "global"): list(rows)}
        for row in rows:
            scopes.setdefault(("region", _contact_scope(row)), []).append(row)
        snapshots = []
        for (scope_type, scope_key), scoped_rows in scopes.items():
            strategy = build_flywheel_strategy(scoped_rows, scope_type=scope_type, scope_key=scope_key, window_start=start.isoformat(), window_end=end.isoformat(), min_samples=min_samples)
            saved = self.repo.save_flywheel_snapshot(strategy)
            self._cache[(scope_type, scope_key)] = saved or strategy
            snapshots.append(saved or strategy)
        learning = self.learn_once(rows=rows)
        return {"status": "completed", "window_days": window_days, "snapshots": snapshots, "learning": learning}

    def learn_once(self, *, rows: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Apply only decision-grade learning actions and record every change."""
        rows = list(rows) if rows is not None else (
            self.repo.list_flywheel_contact_rows(window_days=30)
            if hasattr(self.repo, "list_flywheel_contact_rows") else []
        )
        profile = self.repo.get_active_icp_profile() if hasattr(self.repo, "get_active_icp_profile") else {}
        dashboard = (
            self.repo.outbound_quality_dashboard(user={"id": 0, "role": "admin"})
            if hasattr(self.repo, "outbound_quality_dashboard") else {}
        )
        plan = build_learning_plan(rows, profile=profile, experiments=dashboard.get("experiments") or [])
        applied = []
        skipped = []
        threshold = plan.get("threshold_action")
        if threshold and threshold.get("profile_id") and hasattr(self.repo, "update_icp_profile_threshold"):
            recent_threshold = (
                self.repo.latest_flywheel_learning_event(
                    action_type="adjust_icp_threshold",
                    target_id=int(threshold["profile_id"]),
                    window_days=30,
                )
                if hasattr(self.repo, "latest_flywheel_learning_event") else None
            )
            if recent_threshold:
                skipped.append({"action": "adjust_icp_threshold", "reason": "already_adjusted_within_window"})
                threshold = None
        if threshold and threshold.get("profile_id") and hasattr(self.repo, "update_icp_profile_threshold"):
            updated = self.repo.update_icp_profile_threshold(
                int(threshold["profile_id"]),
                qualified_threshold=int(threshold["to"]),
            )
            event = {
                "action_type": "adjust_icp_threshold",
                "scope_type": "global",
                "scope_key": "global",
                "target_id": int(threshold["profile_id"]),
                "before_state": {"qualified_threshold": threshold["from"]},
                "after_state": {"qualified_threshold": threshold["to"]},
                "reason": threshold["reason"],
                "evidence": plan["calibration"],
            }
            if hasattr(self.repo, "record_flywheel_learning_event"):
                self.repo.record_flywheel_learning_event(event)
            applied.append({"action": "adjust_icp_threshold", "profile_id": threshold["profile_id"], "threshold": threshold["to"], "updated": bool(updated)})
        elif threshold:
            skipped.append({"action": "adjust_icp_threshold", "reason": "repository_not_ready"})

        for action in plan["experiment_actions"]:
            experiment_id = action.get("experiment_id")
            current = next((item for item in dashboard.get("experiments") or [] if item.get("id") == experiment_id), {})
            if current.get("winner_variant") == action["variant"]:
                continue
            if not experiment_id or not hasattr(self.repo, "set_outbound_experiment_winner"):
                skipped.append({"action": "select_experiment_winner", "experiment_id": experiment_id, "reason": "repository_not_ready"})
                continue
            updated = self.repo.set_outbound_experiment_winner(int(experiment_id), variant=action["variant"])
            event = {
                "action_type": "select_experiment_winner",
                "scope_type": "global",
                "scope_key": "global",
                "target_id": int(experiment_id),
                "before_state": {"winner_variant": current.get("winner_variant")},
                "after_state": {"winner_variant": action["variant"]},
                "reason": action["reason"],
                "evidence": action["analysis"],
            }
            if hasattr(self.repo, "record_flywheel_learning_event"):
                self.repo.record_flywheel_learning_event(event)
            applied.append({"action": "select_experiment_winner", "experiment_id": experiment_id, "variant": action["variant"], "updated": bool(updated)})
        return {"status": "applied" if applied else "collecting", "plan": plan, "applied": applied, "skipped": skipped}

    def context_for_contact(self, contact: dict[str, Any]) -> dict[str, Any]:
        region = _contact_scope(contact)
        strategies = []
        for scope_type, scope_key in (("region", region), ("global", "global")):
            cache_key = (scope_type, scope_key)
            if cache_key not in self._cache:
                self._cache[cache_key] = self.repo.get_active_flywheel_snapshot(scope_type=scope_type, scope_key=scope_key) if hasattr(self.repo, "get_active_flywheel_snapshot") else None
            item = self._cache[cache_key]
            if item and item.get("status") == "active":
                strategies.append(item)
        if not strategies:
            return {}
        guidance: list[str] = []
        rules: dict[str, Any] = {}
        for item in reversed(strategies):
            item_guidance = item.get("guidance") if isinstance(item.get("guidance"), dict) else {}
            guidance.extend(str(value) for value in item_guidance.get("next_cycle", []) if value)
            item_rules = item.get("rules") if isinstance(item.get("rules"), dict) else {}
            for key in ("preferred_regions", "preferred_industries", "preferred_roles"):
                if item_rules.get(key):
                    rules[key] = list(dict.fromkeys([*rules.get(key, []), *item_rules[key]]))
        return {"scope": region, "strategies": strategies, "rules": rules, "prompt_guidance": " ".join(dict.fromkeys(guidance)), "score_adjustment": self.score_adjustment(contact, rules=rules)}

    def score_adjustment(self, contact: dict[str, Any], *, rules: dict[str, Any] | None = None) -> int:
        if rules is None:
            rules = self.context_for_contact(contact).get("rules") or {}
        values = (_contact_scope(contact), str(contact.get("industry") or "").strip(), str(contact.get("job_title") or "").strip())
        lists = (rules.get("preferred_regions") or [], rules.get("preferred_industries") or [], rules.get("preferred_roles") or [])
        matches = sum(bool(value) and any(str(value).lower() in str(item).lower() or str(item).lower() in str(value).lower() for item in candidates) for value, candidates in zip(values, lists))
        return min(5, matches)


__all__ = [
    "DataFlywheelService",
    "build_flywheel_metrics",
    "build_flywheel_strategy",
    "build_icp_calibration_examples",
    "build_learning_plan",
    "normalize_region",
]
