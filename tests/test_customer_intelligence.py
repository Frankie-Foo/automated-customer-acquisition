from sales_automation.customer_intelligence import build_customer_profile, next_best_action, outreach_framework, score_customer


def test_customer_score_prioritizes_high_value_luxury_account():
    contact = {
        "job_title": "Founder",
        "company_name": "Luxury Watch Boutique",
        "industry": "pre-owned luxury watches",
        "email": "founder@example.com",
        "email_status": "valid",
        "company_domain": "example.com",
        "linkedin_url": "https://linkedin.com/in/example",
        "location": "India",
        "sabcd_stage": "D",
        "source_context": {
            "seed_category": "second hand luxury platform",
            "seed_reason": "Strong fit for certified pre-owned luxury retail.",
        },
    }

    profile = build_customer_profile(contact)

    assert profile["icp_fit_score"] >= 70
    assert profile["intent_level"] == "medium"
    assert "fit_score_breakdown" in profile
    assert profile["email_framework"]["low_barrier_ask"]
    assert profile["pain_point_strategy"]["suspected_pain"]
    assert len(profile["followup_plan"]) == 4
    assert profile["followup_plan"][0]["day"] == "Day 1"


def test_customer_score_penalizes_closed_loop_status():
    contact = {
        "job_title": "CEO",
        "company_name": "Luxury Retailer",
        "industry": "luxury retail",
        "email": "ceo@example.com",
        "email_status": "valid",
        "status": "unsubscribed",
        "sabcd_stage": "A",
    }

    parts = score_customer(contact)
    action = next_best_action(contact)

    assert parts["risk_penalty"] < 0
    assert "stop outreach" in action


def test_outreach_framework_has_five_parts():
    framework = outreach_framework({
        "company_name": "Test Co",
        "job_title": "Owner",
        "source_context": {"seed_reason": "They operate premium retail stores."},
    })

    assert set(framework) == {"intent", "business_match", "our_value", "low_barrier_ask", "close"}


def test_russia_hiring_signal_improves_account_context_and_why_now():
    signal = "Public hh.ru hiring activity suggests Mercury is building its retail team in Moscow."
    profile = build_customer_profile({
        "company_name": "Mercury",
        "job_title": "Commercial Director",
        "industry": "luxury retail",
        "location": "Moscow, Russia",
        "source_context": {
            "hiring_signal_summary": signal,
            "expansion_score": 85,
        },
    })

    assert profile["fit_score_breakdown"]["account_context"] >= 18
    assert profile["why_now"] == signal
    assert "public hiring signal" in profile["pain_point_strategy"]["message_hook"]
