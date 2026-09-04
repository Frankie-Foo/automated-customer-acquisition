from sales_automation.outbound_quality import (
    assess_icp,
    calibration_summary,
    classify_reply,
    enrichment_readiness,
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


def test_icp_rejects_company_only_and_procurement_records_before_enrichment():
    company_only = assess_icp({"company_name": "Luxury Store", "linkedin_url": "https://linkedin.com/company/store"})
    procurement = enrichment_readiness(
        {
            "first_name": "Pat",
            "last_name": "Lee",
            "job_title": "Senior Procurement Manager",
            "company_name": "Consumer Appliance Manufacturer",
            "location": "Malaysia",
            "linkedin_url": "https://linkedin.com/in/pat-lee",
        },
        paid=True,
    )

    assert company_only["tier"] == "disqualified"
    assert {"missing_person_identity", "missing_role"} <= set(company_only["disqualifiers"])
    assert not procurement["ok"]
    assert procurement["score"] < 70


def test_paid_enrichment_accepts_channel_decision_maker_without_email():
    readiness = enrichment_readiness(_strong_contact(email=None, email_status="unknown"), paid=True)

    assert readiness["ok"]
    assert readiness["tier"] in {"priority", "qualified"}


def test_paid_enrichment_accepts_explicit_dealer_role():
    readiness = enrichment_readiness(
        {
            "first_name": "Carson",
            "last_name": "L.",
            "job_title": "Subsidiary Dealer",
            "company_name": "DJI",
            "location": "Malaysia",
            "linkedin_url": "https://linkedin.com/in/carson",
        },
        paid=True,
    )

    assert readiness["ok"]


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


def test_copy_review_blocks_draft_written_for_the_wrong_contact():
    body = (
        "Hi Bob,\n\nI work with VERTU's international channel development team. Another Retail's established "
        "luxury customer channel may be relevant to a selective partnership assessment. The commercial question is "
        "whether VERTU can complement the current portfolio, service model and retail experience without creating "
        "operational distraction. Two routes could be a controlled shop-in-shop format or selective local distribution. "
        "Any option would need validation against customer fit, location economics and operating responsibilities. "
        "This is a working hypothesis rather than an assumption about the business. I can share a concise market-specific "
        "outline covering the product mix, channel format and practical next steps for an initial assessment. Would that "
        "be useful?\n\nBest regards,\nFrank\n\nUnsubscribe: {{unsubscribe_url}}"
    )

    review = review_email_copy(
        "VERTU x Another Retail",
        body,
        contact={"first_name": "Amy", "company_name": "Premium Retail"},
    )

    assert review["status"] == "blocked"
    assert {item["code"] for item in review["blocking_issues"]} >= {
        "recipient_name_mismatch",
        "company_not_grounded",
    }


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


def test_copy_review_hard_blocks_word_limits_and_fake_urgency():
    too_short = review_email_copy(
        "VERTU x Premium Retail",
        "Hi Amy,\n\nWould a short market partnership discussion be useful?\n\n"
        "Best regards,\nFrank\n\nUnsubscribe: {{unsubscribe_url}}",
    )
    fake_urgency = review_email_copy(
        "VERTU x Premium Retail",
        "Hi Amy,\n\nI work with VERTU's international channel team. Premium Retail's luxury channel may be relevant "
        "to a selective boutique or distribution assessment. The purpose is to understand whether the customer base, "
        "portfolio and service model support a credible adjacent category. We would assess product mix, local demand, "
        "store format and operating responsibilities before either side assumes a business case. Two practical routes "
        "could be a controlled shop-in-shop format or selective distribution for established private clients. This is "
        "only a commercial hypothesis based on the stated channel profile. Act now because this is a limited time offer. "
        "Would a short market-specific discussion be useful?\n\nBest regards,\nFrank\n\n"
        "Unsubscribe: {{unsubscribe_url}}",
    )

    assert "body_below_target_words" in {item["code"] for item in too_short["blocking_issues"]}
    assert "fake_urgency" in {item["code"] for item in fake_urgency["blocking_issues"]}


def test_copy_review_blocks_unsupported_travel_cases_and_product_news():
    base = (
        "Hi Amy,\n\nI work with VERTU's international channel development team. Premium Retail's existing luxury "
        "customer channel may be relevant to a selective partnership assessment. The commercial question is whether "
        "VERTU can complement the current portfolio, service model and retail experience without creating operational "
        "distraction. Two routes could be a controlled shop-in-shop format or selective local distribution. Any option "
        "would need to be assessed against local demand, product mix, customer fit and operating responsibilities. "
        "{claim} This is a working hypothesis rather than an assumed commercial outcome. Would a short market-specific "
        "discussion be useful?\n\nBest regards,\nFrank\n\nUnsubscribe: {{{{unsubscribe_url}}}}"
    )
    claims = {
        "unsupported_travel_or_meeting": "I will be visiting Singapore next month.",
        "unsupported_case_study": "We have helped several local partners grow this category.",
        "unsupported_product_news": "VERTU will launch a new product this month.",
    }

    for expected_code, claim in claims.items():
        review = review_email_copy("VERTU x Premium Retail", base.format(claim=claim))
        assert expected_code in {item["code"] for item in review["blocking_issues"]}


def test_copy_review_blocks_unverified_strategy_claims():
    base = (
        "Hi Amy,\n\nI work with VERTU's international channel development team. Premium Retail's existing luxury "
        "customer channel may be relevant to a selective partnership assessment. The commercial question is whether "
        "VERTU can complement the current portfolio and service model without creating operational distraction. "
        "Two routes could be a controlled boutique format or selective local distribution. Any option would need to "
        "be assessed against local demand, product mix, customer fit and operating responsibilities. {claim} This is a "
        "working hypothesis rather than an assumed commercial outcome. Would a short market-specific discussion be "
        "useful?\n\nBest regards,\nApril Yang\n\nUnsubscribe: {{{{unsubscribe_url}}}}"
    )
    claims = {
        "unsupported_market_stat": "The market will reach $30 billion by 2030.",
        "unsupported_local_commitment": "We are committed to local assembly through an SKD model.",
        "unsupported_partner_progress": "We have already identified several regional partners.",
        "unsupported_partner_deadline": "We are finalizing our partners by January.",
    }

    for expected_code, claim in claims.items():
        review = review_email_copy("Strategic fit: VERTU x Premium Retail", base.format(claim=claim))
        assert expected_code in {item["code"] for item in review["blocking_issues"]}


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


def test_reply_classifier_advances_confirmed_meetings_and_discussions():
    meeting = classify_reply(
        "Re: Vertu",
        "Looking forward to our call on Monday at 11 AM. Please send the meeting details.",
    )
    discussion = classify_reply(
        "Re: Vertu",
        "We are interested to take this discussion forward and await the NDA.",
    )

    assert meeting["label"] == "positive_meeting"
    assert meeting["positive"]
    assert meeting["lifecycle_stage"] == "meeting"
    assert discussion["label"] == "positive_conversation"
    assert discussion["positive"]
    assert discussion["lifecycle_stage"] == "conversation"


def test_reply_classifier_ignores_quoted_outbound_history():
    reply = classify_reply(
        "Re: Vertu",
        "Sure, please share the details for us to study.\n\n"
        "On Monday, April wrote:\n> Would this be relevant?\n> If not interested, let me know.",
    )

    assert reply["label"] == "positive_soft"
    assert reply["should_advance"]


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
