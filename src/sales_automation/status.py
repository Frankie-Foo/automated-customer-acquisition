from __future__ import annotations

TERMINAL_STATUSES = {"replied", "bounced", "unsubscribed"}

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "new": {"enriched"},
    "enriched": {"queued"},
    "queued": {"sent_1"},
    "sent_1": {"sent_2", "replied", "bounced", "unsubscribed"},
    "sent_2": {"sent_3", "replied", "bounced", "unsubscribed"},
    "sent_3": {"replied", "bounced", "unsubscribed"},
    "replied": set(),
    "bounced": set(),
    "unsubscribed": set(),
}


def validate_status(status: str) -> str:
    if status not in ALLOWED_TRANSITIONS:
        raise ValueError(f"Unknown contact status: {status}")
    return status


def can_transition(current: str, next_status: str, *, manual: bool = False) -> bool:
    validate_status(current)
    validate_status(next_status)
    if manual:
        return True
    return next_status in ALLOWED_TRANSITIONS[current]


def next_sent_status(step: int) -> str:
    if step not in (1, 2, 3):
        raise ValueError("sequence step must be 1, 2, or 3")
    return f"sent_{step}"

