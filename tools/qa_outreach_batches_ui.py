"""Isolated browser check with synthetic data; no login or real sends."""
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright


BASE = "http://127.0.0.1:5174"
HTML = """<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body><main id="audit"></main><script type="module">
import RefreshRuntime from '/static/@react-refresh';
RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;
window.__vite_plugin_react_preamble_installed__=true;
import React from '/static/node_modules/.vite/deps/react.js';
import ReactDOM from '/static/node_modules/.vite/deps/react-dom_client.js';
import '/static/src/legacy-styles.css';
const {default:Batches}=await import('/static/src/OutreachBatches.jsx');
ReactDOM.createRoot(document.getElementById('audit')).render(React.createElement(Batches));
</script></body></html>"""


def main():
    out = Path("outputs/qa-outreach-batches")
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        batch = {"id": 999, "name": "QA sample", "owner_name": "April", "total_count": 51}

        def api(route):
            url = urlparse(route.request.url)
            if url.path == "/api/outreach-batches":
                data = {"batches": [batch]}
            else:
                query = parse_qs(url.query)
                offset = int(query.get("offset", [0])[0])
                total = 1 if query.get("search") else 51
                data = {"batch": batch, "total": total,
                    "summary": {"total_count": 51, "sent_contacts": 1, "sent_messages": 2},
                    "contacts": [{"id": i + 1, "first_name": f"Buyer {i + 1}",
                        "company_name": "Example Retail", "email": "buyer@example.test",
                        "latest_subject": "VERTU sample", "latest_body": "<script>not executable</script>\nDraft content",
                        "lifecycle_stage": "lead", "status": "enriched", "sent_count": 0,
                        "replied_count": 0, "opened_count": 0} for i in range(offset, min(offset + 25, total))]}
            route.fulfill(content_type="application/json", body=json.dumps({"ok": True, "data": data}))

        page.route(BASE + "/api/outreach-batches**", api)
        page.route(BASE + "/audit-preview", lambda route: route.fulfill(content_type="text/html", body=HTML))
        page.goto(BASE + "/audit-preview")
        try:
            page.locator(".outreach-batches-table tbody tr").first.wait_for(timeout=15000)
        except Exception:
            print("Browser errors:", errors)
            raise
        assert page.locator(".outreach-batches-table tbody tr").count() == 25
        page.get_by_label("下一页", exact=True).click()
        page.get_by_role("button", name="Buyer 26", exact=True).wait_for()
        page.get_by_label("下一页", exact=True).click()
        page.get_by_role("button", name="Buyer 51", exact=True).wait_for()
        assert page.locator(".outreach-batches-table tbody tr").count() == 1
        page.locator("input[type=search]").fill("unique")
        page.get_by_role("button", name="搜索", exact=True).click()
        page.get_by_role("button", name="Buyer 1", exact=True).wait_for()
        page.locator(".outreach-batches-message summary").click()
        assert "not executable" in page.locator(".outreach-batches-message dd").last.inner_text()
        for width in (1440, 390):
            page.set_viewport_size({"width": width, "height": 960})
            page.screenshot(path=str(out / f"batch-{width}.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), f"Page overflow at {width}"

        # Reopen through a non-batch entry point, then approve without losing attribution.
        writes = []
        unexpected = []

        def workspace_api(route):
            path = urlparse(route.request.url).path
            if path == "/api/contacts":
                data = {"contacts": []}
            elif path == "/api/contact-detail":
                data = {"contact": {"id": 123, "first_name": "Test buyer", "company_name": "Example Retail",
                            "email": "test@example.test", "email_status": "valid", "pool_type": "private"},
                        "draft": {"contact_id": 123, "campaign_id": 7, "subject": "Saved subject",
                                  "body": "Saved body", "mode": "custom", "status": "draft"}}
            elif path == "/api/email-draft":
                writes.append(route.request.post_data_json)
                data = {"subject": "Saved subject", "body": "Saved body", "campaign_id": 7}
            elif path == "/api/email-draft/approve":
                data = {}
            else:
                unexpected.append(path)
                route.abort()
                return
            route.fulfill(content_type="application/json", body=json.dumps({"ok": True, "data": data}))

        page.route(BASE + "/api/**", workspace_api)
        page.evaluate("""async () => {
            sessionStorage.setItem('salesbot:selected-contact-id', '123');
            sessionStorage.removeItem('salesbot:selected-outreach-batch');
            const host = document.createElement('div');
            host.id = 'customer-workspace';
            host.innerHTML = '<div id="react-workspace-root"></div>';
            document.body.appendChild(host);
            const React = (await import('/static/node_modules/.vite/deps/react.js')).default;
            const ReactDOM = (await import('/static/node_modules/.vite/deps/react-dom_client.js')).default;
            const {default: Workspace} = await import('/static/src/CustomerWorkspace.jsx');
            ReactDOM.createRoot(document.createElement('div')).render(React.createElement(Workspace));
        }""")
        page.get_by_role("button", name="2 确认内容", exact=True).click()
        page.get_by_text("草稿已审核锁定", exact=True).wait_for()
        assert len(writes) == 1 and writes[0]["campaign_id"] == 7, writes
        assert not unexpected, unexpected
        assert not errors, errors
        browser.close()
    print("PASS: pagination, search, escaped body, desktop/mobile, reopened draft campaign, no sends or JS errors")


if __name__ == "__main__":
    main()
