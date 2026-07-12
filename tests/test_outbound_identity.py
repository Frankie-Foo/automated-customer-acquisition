from types import SimpleNamespace

from sales_automation.outbound_identity import outbound_sender, parse_signed_reply_route, sender_alias_localpart, signed_reply_address


def config(*, mode="centralized_alias"):
    return SimpleNamespace(
        raw={
            "outbound_identity": {
                "mode": mode,
                "sending_domain": "outreach.vertu.test",
                "reply_domain": "reply.outreach.vertu.test",
                "routing_secret": "routing-secret-at-least-24-characters",
                "reply_localpart": "reply",
            }
        }
    )


def test_outbound_sender_uses_sales_alias_and_keeps_transport_email():
    transport = {"id": 3, "name": "Transport", "email": "api@mail.example", "provider": "resend", "dry_run": False}
    user = {"id": 11, "username": "Viki", "display_name": "Viki", "sender_alias_localpart": "viki.you"}

    sender = outbound_sender(config(), user, transport)

    assert sender["email"] == "viki.you@outreach.vertu.test"
    assert sender["name"] == "Viki"
    assert sender["transport_email"] == "api@mail.example"
    assert sender["id"] == 3


def test_signed_reply_address_round_trip_and_tamper_rejection():
    cfg = config()
    address = signed_reply_address(cfg, contact_id=385, user_id=11, sequence_step=2)

    route = parse_signed_reply_route(cfg, ["Other <other@example.com>", f"VERTU Replies <{address}>"])

    assert route == {"contact_id": 385, "user_id": 11, "sequence_step": 2, "address": address}
    assert parse_signed_reply_route(cfg, address.replace(".385.", ".386.")) is None


def test_legacy_mode_preserves_transport_sender_and_has_no_route():
    transport = {"name": "Transport", "email": "api@mail.example"}

    assert outbound_sender(config(mode="legacy"), {"username": "Viki"}, transport) == transport
    assert signed_reply_address(config(mode="legacy"), contact_id=1, user_id=2, sequence_step=1) is None


def test_alias_falls_back_to_stable_user_id_for_non_ascii_username():
    assert sender_alias_localpart({"id": 27, "username": "王宇彤"}) == "sales-27"


def test_smtp_uses_authenticated_from_address_unless_alias_is_allowed():
    cfg = config()
    cfg.raw["smtp"] = {"allow_from_alias": False}
    transport = {"provider": "smtp", "name": "Mailbox", "email": "partnerships@outreach.vertu.test"}

    sender = outbound_sender(cfg, {"id": 11, "username": "Viki", "display_name": "Viki"}, transport)

    assert sender["name"] == "Viki"
    assert sender["email"] == "partnerships@outreach.vertu.test"


def test_smtp_can_use_per_user_alias_when_server_grants_send_as_permission():
    cfg = config()
    cfg.raw["smtp"] = {"allow_from_alias": True}
    transport = {"provider": "smtp", "name": "Mailbox", "email": "partnerships@outreach.vertu.test"}

    sender = outbound_sender(cfg, {"id": 11, "username": "Viki", "display_name": "Viki"}, transport)

    assert sender["email"] == "viki@outreach.vertu.test"
