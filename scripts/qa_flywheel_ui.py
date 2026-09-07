"""Isolated browser fixture: no real authentication, customers or API writes."""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright, expect


HTML = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<body><p>QA fixture - simulated data, not production</p><div id="fixture-mount"></div><div id="admin-console"></div>
<script type="module">
import RefreshRuntime from '/static/@react-refresh';
RefreshRuntime.injectIntoGlobalHook(window);
window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => (type) => type;
window.__vite_plugin_react_preamble_installed__ = true;
window.SALESBOT_SESSION = {user:{id:1,role:'admin'}};
const {default: React} = await import('/static/node_modules/.vite/deps/react.js');
const {default: ReactDOM} = await import('/static/node_modules/.vite/deps/react-dom_client.js');
const {default: Admin} = await import('/static/src/AdminConsole.jsx');
await import('/static/src/legacy-styles.css');
ReactDOM.createRoot(document.getElementById('fixture-mount')).render(React.createElement(Admin));
</script></body></html>'''


def main():
    out = Path('artifacts/flywheel-qa')
    out.mkdir(parents=True, exist_ok=True)
    experiment = {
        'id': 1, 'name': 'QA comparison', 'status': 'active', 'winner_variant': None,
        'analysis': {'decision_reason': 'uncertain_difference', 'variants': [
            {'name': 'A', 'sent': 120, 'replies': 8, 'positive_replies': 5,
             'positive_reply_rate': 4.17, 'bounced': 2, 'unsubscribed': 0},
            {'name': 'B', 'sent': 120, 'replies': 10, 'positive_replies': 6,
             'positive_reply_rate': 5.0, 'bounced': 1, 'unsubscribed': 0}]}}
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/api/**', lambda route: route.fulfill(
            json={'experiments': [experiment]} if '/outbound-quality' in route.request.url else {}))
        page.route('**/qa-flywheel', lambda route: route.fulfill(content_type='text/html', body=HTML))
        page.goto('http://127.0.0.1:18771/qa-flywheel')
        page.wait_for_timeout(1500)
        assert not errors, errors
        page.get_by_role('button', name='质量与实验', exact=True).click()
        for width, height in [(1440, 1000), (390, 844)]:
            page.set_viewport_size({'width': width, 'height': height})
            panel = page.locator('.experiment-history')
            panel.scroll_into_view_if_needed()
            assert panel.get_by_text('差异尚不明确', exact=False).is_visible()
            assert panel.get_by_text('当前执行：各版本均衡对照。', exact=True).is_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            panel.screenshot(path=str(out / f'experiments-{width}.png'))
        assert not errors, errors
        performance_html = HTML.replace("/static/src/AdminConsole.jsx", "/static/src/EmailPerformance.jsx").replace("role:'admin'", "role:'sales'")
        page.route('**/qa-performance', lambda route: route.fulfill(content_type='text/html', body=performance_html))
        mode = {'value': 'normal'}
        calls = []
        weeks = [
            {'week': '2026-09-07', 'sent': 12, 'matured': 0, 'replied': 0, 'positive': 0, 'bounced': 0, 'unsubscribed': 0},
            {'week': '2026-08-17', 'sent': 100, 'matured': 100, 'replied': 15, 'positive': 10, 'bounced': 2, 'unsubscribed': 0},
            {'week': '2026-08-10', 'sent': 50, 'matured': 40, 'replied': 1, 'positive': 0, 'bounced': 8, 'unsubscribed': 1},
            {'week': '2026-08-03', 'sent': 20, 'matured': 20, 'replied': 0, 'positive': 0, 'bounced': 0, 'unsubscribed': 0},
        ]

        def performance_api(route):
            calls.append(route.request.url)
            if mode['value'] == 'error':
                route.fulfill(status=503, json={'error': 'QA service unavailable'})
            else:
                route.fulfill(json={'scope': 'current_owner', 'weeks': [] if mode['value'] == 'empty' else weeks})

        page.route('**/api/email-performance?*', performance_api)
        page.goto('http://127.0.0.1:18771/qa-performance')
        panel = page.get_by_role('region', name='邮件效果复盘')
        expect(panel.get_by_text('已有正向回复', exact=True)).to_be_visible()
        for width, height in [(1440, 1000), (390, 844), (320, 740)]:
            page.set_viewport_size({'width': width, 'height': height})
            expect(panel.get_by_text('需排查', exact=True)).to_be_visible()
            expect(panel.locator('.performance-week').first.get_by_text('待观察', exact=True)).to_have_count(3)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            assert panel.bounding_box()['width'] >= width * .7
            panel.screenshot(path=str(out / f'performance-{width}.png'))
        page.get_by_role('button', name='查看全部 4 周', exact=True).click()
        expect(panel.locator('.performance-week')).to_have_count(4)
        page.get_by_role('button', name='收起历史周', exact=True).click()
        expect(panel.locator('.performance-week')).to_have_count(3)
        for days in ('7', '30', '14'):
            page.get_by_label('邮件效果观察期').select_option(days)
            expect(panel.get_by_text('已有正向回复', exact=True)).to_be_visible()
            assert calls[-1].endswith('days=' + days)
        mode['value'] = 'error'
        page.get_by_label('邮件效果观察期').select_option('7')
        expect(page.get_by_role('alert')).to_contain_text('QA service unavailable')
        mode['value'] = 'empty'
        page.get_by_role('button', name='重试', exact=True).click()
        expect(panel.get_by_text('最近90天暂无真实发送记录。', exact=True)).to_be_visible()
        mode['value'] = 'normal'
        page.evaluate("window.dispatchEvent(new CustomEvent('salesbot:refresh-related'))")
        expect(panel.get_by_text('已有正向回复', exact=True)).to_be_visible()
        assert not errors, errors
        browser.close()
    print(json.dumps({'result': 'PASS', 'viewports': [1440, 390, 320], 'page_errors': errors,
                      'performance': 'window switching, expand/collapse, error/retry, empty/refresh'}))


if __name__ == '__main__':
    main()
