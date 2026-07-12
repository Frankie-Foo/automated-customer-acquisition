from __future__ import annotations

from typing import Any


SABCD_STAGES = ("D", "C", "B", "A", "S")

SABCD_LABELS = {
    "D": "未接触",
    "C": "已触达",
    "B": "多轮沟通",
    "A": "商业计划/试订单",
    "S": "签约建店",
}

_SABCD_RANK = {stage: index for index, stage in enumerate(SABCD_STAGES)}


def validate_sabcd_stage(stage: str) -> str:
    normalized = str(stage or "").strip().upper()
    if normalized not in SABCD_STAGES:
        raise ValueError(f"Unsupported sabcd_stage: {stage}")
    return normalized


def derive_sabcd_stage(
    *,
    status: str | None = None,
    lifecycle_stage: str | None = None,
    disposition: str | None = None,
    sequence_step: int | None = None,
    current: str | None = None,
) -> str:
    """Derive the sales SABCD funnel stage from existing operational state.

    D: not contacted yet.
    C: contacted/touched.
    B: active multi-round communication.
    A: business plan, trial order, agreement, or store setup discussion.
    S: signed/maintenance.
    """

    lifecycle_stage = str(lifecycle_stage or "").strip()
    status = str(status or "").strip()
    disposition = str(disposition or "").strip()
    current = str(current or "").strip().upper()
    try:
        sequence_step_value = int(sequence_step or 0)
    except Exception:
        sequence_step_value = 0

    candidate = "D"
    if lifecycle_stage in {"signed", "maintenance"} or disposition == "won":
        candidate = "S"
    elif lifecycle_stage in {"business_plan", "trial_order", "agency_agreement", "store_creation", "store_visit", "hq_visit"}:
        candidate = "A"
    elif lifecycle_stage in {"conversation", "meeting"}:
        candidate = "B"
    elif lifecycle_stage == "replied" or status == "replied":
        candidate = "C"
    elif status in {"sent_1", "sent_2", "sent_3"} or sequence_step_value > 0:
        candidate = "C"

    return advance_sabcd_stage(current or "D", candidate)


def advance_sabcd_stage(current: str | None, candidate: str | None) -> str:
    """Advance an automated funnel stage without allowing a downgrade."""

    current_stage = str(current or "D").strip().upper()
    candidate_stage = str(candidate or current_stage).strip().upper()
    if current_stage not in SABCD_STAGES:
        current_stage = "D"
    if candidate_stage not in SABCD_STAGES:
        return current_stage
    return candidate_stage if _SABCD_RANK[candidate_stage] > _SABCD_RANK[current_stage] else current_stage


def stage_from_payload(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return validate_sabcd_stage(str(value))
