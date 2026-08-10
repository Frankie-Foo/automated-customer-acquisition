from sales_automation.outbound_quality import (
    assess_icp,
    calibration_summary,
    classify_reply,
    prospect_copy_word_count,
    review_email_copy,
    score_lead_list,
    summarize_experiment,
)
from sales_automation.services.quality import OutboundQualityService


def _strong_contact(**overrides):
    contact = {
        "first_name": "Amy",
        "last_name": "Tan",
        "job_title": "Managing Director",
        "company_name": "Premium Retail Group",
        "company_domain": "premium.example",
        "industry": "luxury retail",
        "location": "Singapore",
        "email": "amy.tan@premium.example",
        "email_status": "valid",
        "linkedin_url": "https://linkedin.com/in/amy-tan",
        "identity_confidence": 90,
        "source_context": {"seed_reason": "premium distributor with an expanding retail network"},
    }
    contact.update(overrides)
    return contact


def test_icp_assessment_is_explainable_and_blocks_bad_roles():
    strong = assess_icp(_strong_contact())
    weak = assess_icp(_strong_contact(job_title="Customer Service Assistant"))

    assert strong["qualified"]
    assert strong["score"] >= 80
    assert strong["breakdown"]["account_fit"] == 25
    assert weak["tier"] == "disqualified"
    assert "low_value_role" in weak["disqualifiers"]


def test_icp_qualified_flag_follows_the_configured_threshold():
    contact = _strong_contact(
        email_status="unknown",
        linkedin_url="",
        identity_confidence=0,
        source_context={},
        location="",
    )

    assessment = assess_icp(contact, {"qualified_threshold": 75, "review_threshold": 50})

    assert assessment["score"] < 75
    assert assessment["tier"] == "review"
    assert not assessment["qualified"]


def test_list_scorecard_uses_quality_dimensions_and_flags_duplicates():
    rows = [
        _strong_contact(),
        _strong_contact(first_name="Ben", email="amy.tan@premium.example"),
        _strong_contact(
            first_name="Office",
            last_name="Team",
            email="info@premium.example",
            job_title="Assistant",
        ),
    ]

    scorecard = score_lead_list(rows)

    assert scorecard["total"] == 3
    assert "duplicate_emails" in scorecard["issues"]
    assert "role_based_emails" in scorecard["issues"]
    assert scorecard["sample_warning"]


def test_copy_review_blocks_internal_fields_and_unresolved_placeholders():
    review = review_email_copy(
        "Partnership for [Company]",
        "Hi Amy,\n\nLead score: 85. Verification status: valid. "
        "Would it be useful to discuss a selective retail partnership?\n\nUnsubscribe: {{unsubscribe_url}}",
    )

    assert review["status"] == "blocked"
    assert {item["code"] for item in review["blocking_issues"]} >= {
        "unresolved_placeholders",
        "internal_data_exposed",
    }


def test_copy_review_accepts_concise_specific_email():
    review = review_email_copy(
        "A Vertu retail fit for Premium Retail",
        "Hi Amy,\n\nI noticed Premium Retail is expanding its luxury retail network in Singapore. "
        "Your role suggests you may be close to decisions about new categories and local customer experience. "
        "From VERTU headquarters, I work with local operators assessing whether a VERTU boutique or selective distribution model suits their market. "
        "VERTU combines luxury mobile products, accessories, and a differentiated retail experience for high-value customers. "
        "The practical question is whether that category can complement your existing portfolio, customer profile, and store operating model without assuming a commercial outcome. "
        "May I send a one-page view of how a VERTU channel partnership could be assessed for Singapore?\n\n"
        "Best regards,\nFrank\n\nUnsubscribe: {{unsubscribe_url}}",
    )

    assert review["status"] == "ready"
    assert review["score"] >= 80
    assert review["rules"]["peer_to_peer"]
    assert review["rules"]["word_count_in_range"]
    assert review["rules"]["single_low_friction_cta"]


def test_copy_review_flags_salesy_language_and_meeting_first_cta():
    review = review_email_copy(
        "Exclusive opportunity for Premium Retail",
        "Dear Sir or Madam,\n\nHope this email finds you well. We are the world's leading luxury mobile brand "
        "and have an exclusive opportunity for Premium Retail. Our products are high quality and good price, with a "
        "guaranteed return for every partner. We believe your esteemed company would benefit immediately from this offer. "
        "Could we book a 30-minute meeting next week to discuss the opportunity?\n\n"
        "Best regards,\nFrank\n\nUnsubscribe: {{unsubscribe_url}}",
    )

    warning_codes = {item["code"] for item in review["warnings"]}

    assert review["status"] == "blocked"
    assert {"template_cliche", "salesy_pitch", "high_friction_cta"} <= warning_codes
    assert "unverifiable_return" in {item["code"] for item in review["blocking_issues"]}
    assert not review["rules"]["peer_to_peer"]
    assert not review["rules"]["single_low_friction_cta"]


def test_copy_review_blocks_unverifiable_commercial_returns():
    review = review_email_copy(
        "A Vertu retail fit for Premium Retail",
        "Hi Amy,\n\nA VERTU partnership guarantees a 200% return for every local operator. "
        "Your luxury retail network looks relevant to a selective distribution discussion. "
        "May I send a one-page local-market outline?\n\n"
        "Best regards,\nFrank\n\nUnsubscribe: {{unsubscribe_url}}",
    )

    assert review["status"] == "blocked"
    assert "unverifiable_return" in {item["code"] for item in review["blocking_issues"]}


def test_prospect_copy_word_count_excludes_signature_and_unsubscribe_line():
    body = "Hi Amy,\n\nOne two three four.\n\nBest regards,\nFrank\n\nUnsubscribe: {{unsubscribe_url}}"

    assert prospect_copy_word_count(body) == 6


def test_reply_classifier_separates_positive_ooo_and_rejection():
    positive = classify_reply("Re: Vertu", "Please send pricing and let us schedule a meeting.")
    ooo = classify_reply("Automatic reply", "I am out of office until Monday.")
    rejection = classify_reply("Re: Vertu", "This is not relevant for our business.")

    assert positive["label"] == "positive_interested"
    assert positive["positive"]
    assert positive["should_advance"]
    assert ooo["label"] == "ooo"
    assert not ooo["should_advance"]
    assert rejection["label"] == "negative_notfit"


def test_experiment_summary_waits_for_sample_then_selects_positive_reply_winner():
    collecting = summarize_experiment(
        [
            {"name": "A", "sent": 30, "positive_replies": 2},
            {"name": "B", "sent": 30, "positive_replies": 4},
        ]
    )
    ready = summarize_experiment(
        [
            {"name": "A", "sent": 120, "positive_replies": 5},
            {"name": "B", "sent": 120, "positive_replies": 10},
        ]
    )

    assert collecting["winner"] is None
    assert ready["winner"] == "B"


def test_future_experiment_assignment_uses_learned_winner():
    class Repo:
        def get_active_outbound_experiment(self, *, owner_user_id):
            return {
                "id": 3,
                "name": "Subject test",
                "variable_name": "subject",
                "winner_variant": "B",
                "variants": [
                    {"name": "A", "instruction": "Lead with category."},
                    {"name": "B", "instruction": "Lead with local fit."},
                ],
            }

    assignment = OutboundQualityService(Repo()).experiment_assignment(contact_id=1, owner_user_id=2)

    assert assignment["variant"] == "B"
    assert assignment["winner_selected"]


def test_icp_calibration_recommends_tighter_threshold_after_false_positives():
    feedback = [
        {"predicted_qualified": True, "expected_qualified": False}
        for _ in range(7)
    ] + [
        {"predicted_qualified": True, "expected_qualified": True}
        for _ in range(3)
    ]

    summary = calibration_summary(feedback, current_threshold=70)

    assert summary["false_positive"] == 7
    assert summary["proposed_threshold"] == 75
