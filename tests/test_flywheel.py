from sales_automation.services.flywheel import (
    build_flywheel_metrics,
    build_flywheel_strategy,
    build_icp_calibration_examples,
    build_learning_plan,
    normalize_region,
)


def _row(**overrides):
    row = {"location": "Singapore", "industry": "luxury retail", "job_title": "Founder", "sent": 1, "delivered": 1, "opened": 1, "replied": 0, "positive_replies": 0, "negative_replies": 0, "bounced": 0, "unsubscribed": 0, "meetings": 0, "won": 0, "lost": 0}
    row.update(overrides)
    return row


def test_region_normalization_covers_operating_regions():
    assert normalize_region("Dubai, UAE") == "middle_east"
    assert normalize_region("伊朗") == "middle_east"
    assert normalize_region("Almaty, Kazakhstan") == "cis"
    assert normalize_region("俄罗斯") == "cis"
    assert normalize_region("India") == "south_asia"


def test_metrics_use_contact_level_outcomes_and_rates():
    metrics = build_flywheel_metrics([_row(opened=1, replied=1, positive_replies=1, meetings=1), _row(bounced=1, delivered=0, opened=0)])
    assert metrics["sent"] == 2
    assert metrics["positive_replies"] == 1
    assert metrics["reply_rate"] == 0.5
    assert metrics["bounce_rate"] == 0.5


def test_strategy_does_not_change_rules_on_small_sample():
    strategy = build_flywheel_strategy([_row(positive_replies=1, replied=1)], scope_type="global", scope_key="global", window_start="2026-08-01T00:00:00+00:00", window_end="2026-08-07T00:00:00+00:00", min_samples=5)
    assert strategy["status"] == "insufficient_sample"
    assert strategy["rules"]["do_not_auto_expand"]
    assert strategy["guidance"]["score_adjustment_max"] == 0


def test_strategy_surfaces_positive_segments_after_minimum_sample():
    strategy = build_flywheel_strategy([_row(positive_replies=1, replied=1) for _ in range(5)], scope_type="region", scope_key="southeast_asia", window_start="2026-08-01T00:00:00+00:00", window_end="2026-08-07T00:00:00+00:00", min_samples=5)
    assert strategy["status"] == "active"
    assert strategy["metrics"]["positive_reply_rate"] == 1.0


def test_automatic_learning_ignores_neutral_contacts_and_waits_for_evidence():
    rows = [_row(icp_assessment={"qualified": True}, replied=0) for _ in range(9)]
    rows.append(_row(icp_assessment={"qualified": True}, positive_replies=1, replied=1))

    examples = build_icp_calibration_examples(rows)
    plan = build_learning_plan(rows, profile={"id": 1, "qualified_threshold": 70})

    assert len(examples) == 1
    assert plan["calibration"]["reviewed"] == 1
    assert plan["threshold_action"] is None


def test_automatic_learning_proposes_bounded_icp_update_and_experiment_winner():
    rows = [
        _row(icp_assessment={"qualified": True}, negative_replies=1, replied=1)
        for _ in range(7)
    ] + [
        _row(icp_assessment={"qualified": True}, positive_replies=1, replied=1)
        for _ in range(3)
    ]
    plan = build_learning_plan(
        rows,
        profile={"id": 1, "qualified_threshold": 70},
        experiments=[
            {
                "id": 8,
                "name": "Subject test",
                "status": "active",
                "analysis": {"winner": "B", "recommendation": "Keep B"},
            }
        ],
    )

    assert plan["threshold_action"]["to"] == 75
    assert plan["experiment_actions"][0]["variant"] == "B"
    strategy = build_flywheel_strategy(
        [_row(positive_replies=1, replied=1) for _ in range(5)],
        scope_type="region",
        scope_key="southeast_asia",
        window_start="2026-08-01T00:00:00+00:00",
        window_end="2026-08-07T00:00:00+00:00",
        min_samples=5,
    )
    assert "正向回复" in strategy["guidance"]["prompt_guidance"]
