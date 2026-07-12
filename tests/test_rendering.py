from pathlib import Path

import pytest

from sales_automation.rendering import open_pixel_url, render_string, render_template, unsubscribe_url, verify_tracking_token
from sales_automation.web import static_path


def test_render_string_replaces_missing_with_empty():
    assert render_string("Hi {{first_name}} {{unknown}}", {"first_name": "Ada"}) == "Hi Ada "


def test_render_template_returns_text_and_html(tmp_path: Path):
    path = tmp_path / "template.txt"
    path.write_text("Hi {{first_name}}\n<a>", encoding="utf-8")
    text, html = render_template(path, {"first_name": "Ada"})
    assert text == "Hi Ada\n<a>"
    assert "Hi Ada<br>&lt;a&gt;" == html


def test_web_static_files_are_package_resources():
    index = static_path("index.html")
    assert index.exists()

    html = index.read_text(encoding="utf-8")
    assert 'id="root"' in html
    assert "/static/assets/" in html

    assets_dir = static_path("assets")
    assert assets_dir.exists()
    assert any(path.suffix == ".js" for path in assets_dir.iterdir())
    assert any(path.suffix == ".css" for path in assets_dir.iterdir())


def test_static_path_rejects_traversal():
    with pytest.raises(ValueError):
        static_path("../config.yaml")
    with pytest.raises(ValueError):
        static_path("assets/%2e%2e/config.yaml")


def test_tracking_links_are_signed_and_action_scoped():
    secret = "test-secret-with-at-least-24-characters"
    contact = {"id": 42}
    unsubscribe = unsubscribe_url(contact, "https://sales.example.test", secret)
    pixel = open_pixel_url(contact, 2, "https://sales.example.test", secret)

    unsubscribe_token = unsubscribe.split("token=", 1)[1]
    pixel_token = pixel.split("token=", 1)[1]
    assert verify_tracking_token(unsubscribe_token, "unsubscribe", secret)["contact_id"] == 42
    assert verify_tracking_token(pixel_token, "open", secret)["step"] == 2
    with pytest.raises(ValueError, match="invalid_tracking_action"):
        verify_tracking_token(pixel_token, "unsubscribe", secret)


def test_tracking_link_rejects_tampering_and_expiry():
    secret = "test-secret-with-at-least-24-characters"
    token = unsubscribe_url({"id": 7}, "https://sales.example.test", secret).split("token=", 1)[1]
    with pytest.raises(ValueError, match="invalid_tracking_signature"):
        verify_tracking_token(token + "x", "unsubscribe", secret)

