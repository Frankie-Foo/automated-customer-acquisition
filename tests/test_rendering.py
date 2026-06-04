from pathlib import Path

from sales_automation.rendering import render_string, render_template


def test_render_string_replaces_missing_with_empty():
    assert render_string("Hi {{first_name}} {{unknown}}", {"first_name": "Ada"}) == "Hi Ada "


def test_render_template_returns_text_and_html(tmp_path: Path):
    path = tmp_path / "template.txt"
    path.write_text("Hi {{first_name}}\n<a>", encoding="utf-8")
    text, html = render_template(path, {"first_name": "Ada"})
    assert text == "Hi Ada\n<a>"
    assert "Hi Ada<br>&lt;a&gt;" == html

