from sales_automation.outreach_copy import (
    contains_internal_outreach_data,
    customer_visible_contact,
    customer_visible_source_context,
)


def test_customer_visible_context_drops_internal_import_note():
    contact = {
        "industry": "consumer electronics / 3C数码渠道",
        "source_context": {
            "seed_category": "3C retail",
            "seed_location": "Malaysia",
            "seed_reason": "联系人:Amy | 触达优先级:P0 | 核实状态:已核实 | 客户来源:batch-1",
        },
        "profile_insights": {"email_framework": {"business_match": "unsafe cached text"}},
    }

    visible = customer_visible_contact(contact)

    assert visible["source_context"] == {
        "seed_category": "3C retail",
        "seed_location": "Malaysia",
    }
    assert visible["profile_insights"] == {}
    assert visible["industry"] == "consumer electronics"


def test_customer_visible_context_keeps_plain_public_reason():
    contact = {
        "source_context": {
            "seed_reason": "The company operates a curated luxury resale marketplace in India.",
        }
    }

    assert customer_visible_source_context(contact)["seed_reason"] == (
        "The company operates a curated luxury resale marketplace in India."
    )


def test_internal_marker_detection_covers_chinese_and_english_fields():
    assert contains_internal_outreach_data("是否回复:否")
    assert contains_internal_outreach_data("lead_score=82")
    assert not contains_internal_outreach_data("Best regards,\nIvan You")
