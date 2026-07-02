import pytest

from sales_automation.sabcd import derive_sabcd_stage, stage_from_payload, validate_sabcd_stage


def test_sabcd_stage_derivation_from_outreach_status():
    assert derive_sabcd_stage(status="new") == "D"
    assert derive_sabcd_stage(status="queued") == "D"
    assert derive_sabcd_stage(status="sent_1") == "C"
    assert derive_sabcd_stage(sequence_step=2) == "C"


def test_sabcd_stage_derivation_from_sales_lifecycle():
    assert derive_sabcd_stage(status="replied") == "B"
    assert derive_sabcd_stage(lifecycle_stage="conversation") == "B"
    assert derive_sabcd_stage(lifecycle_stage="business_plan") == "A"
    assert derive_sabcd_stage(lifecycle_stage="trial_order") == "A"
    assert derive_sabcd_stage(lifecycle_stage="signed") == "S"
    assert derive_sabcd_stage(disposition="won") == "S"


def test_sabcd_payload_validation():
    assert stage_from_payload("a") == "A"
    assert stage_from_payload("") is None
    with pytest.raises(ValueError):
        validate_sabcd_stage("x")
