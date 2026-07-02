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
    B: replied or active communication.
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

    if lifecycle_stage in {"signed", "maintenance"} or disposition == "won":
        return "S"
    if lifecycle_stage in {"business_plan", "trial_order", "agency_agreement", "store_creation", "store_visit", "hq_visit"}:
        return "A"
    if lifecycle_stage in {"replied", "conversation", "meeting"} or status == "replied":
        return "B"
    if status in {"sent_1", "sent_2", "sent_3"} or sequence_step_value > 0:
        return "C"
    if status in {"queued"}:
        return "D"
    if current in SABCD_STAGES:
        return current
    return "D"


def stage_from_payload(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return validate_sabcd_stage(str(value))
