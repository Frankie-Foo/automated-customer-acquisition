"""Local-only full-app QA. No .env, provider keys, mailbox worker or scheduler."""
import argparse
import json
from http.server import ThreadingHTTPServer
from pathlib import Path

from sales_automation.config import AppConfig
from sales_automation.db import Database, Repository
from sales_automation.web import make_handler


ROOT = Path(__file__).resolve().parents[1]
URL = 'http://127.0.0.1:18773'
PASSWORD = 'QA-local-only-2026'


def repository():
    config = AppConfig(raw={
        'database': {'host': '127.0.0.1', 'port': 15447, 'dbname': 'salesbot_client_qa',
                     'user': 'postgres', 'password': 'local_qa_only'},
        'app': {'public_base_url': URL}, 'apis': {}, 'sender': {'dry_run': True},
        'quotas': {'global_daily_send': 0, 'global_daily_source': 0},
    }, root_dir=ROOT)
    return config, Repository(Database(config))


def setup(repo):
    repo.db.bind_system_actor()
    applied = repo.db.migrate(ROOT / 'migrations')
    assert repo.db.migrate(ROOT / 'migrations') == []
    users = {}
    for username, role in [('qa_sales', 'sales'), ('qa_other', 'sales'), ('qa_admin', 'admin')]:
        with repo.db.connect() as conn:
            user = conn.execute('SELECT id FROM sales_users WHERE username=%s', (username,)).fetchone()
        users[username] = user or repo.create_user(username=username, password=PASSWORD,
            display_name=username + ' [TEST]', role=role, must_change_password=False,
            daily_send_limit=0, daily_source_limit=0)
    with repo.db.connect() as conn:
        if not conn.execute("SELECT 1 FROM contacts WHERE source='client_qa'").fetchone():
            for owner, count in [('qa_sales', 3), ('qa_other', 1)]:
                uid = users[owner]['id']
                for index in range(count):
                    contact = conn.execute("""INSERT INTO contacts
                        (linkedin_url,first_name,company_name,email,email_status,job_title,owner_user_id,pool_type,source,status)
                        VALUES (%s,%s,%s,%s,'valid','Retail Director',%s,'private','client_qa','sent_1') RETURNING id""",
                        (f'https://example.com/{owner}/{index}', f'QA {owner} {index}', 'QA Example Retail',
                         f'{owner}-{index}@example.com', uid)).fetchone()['id']
                    age = 20 if index != 2 else 2
                    mid = f'<qa-{contact}@example.com>'
                    conn.execute("""INSERT INTO outreach_messages
                        (contact_id,user_id,channel,body,subject,status,provider,provider_message_id,sent_at,metadata)
                        VALUES (%s,%s,'email','QA fixture only','QA partnership review','sent','smtp',%s,
                                NOW()-(%s * INTERVAL '1 day'),'{"dry_run":false}')""", (contact,uid,mid,age))
                    conn.execute("""INSERT INTO email_events(contact_id,sequence_step,event_type,email_subject,message_id,occurred_at,metadata)
                        VALUES (%s,1,'sent','QA partnership review',%s,NOW()-(%s * INTERVAL '1 day'),
                                '{"sender_email":"qa@example.com","dry_run":false}')""", (contact,mid,age))
                    if index == 0:
                        conn.execute("""INSERT INTO interactions(contact_id,user_id,interaction_type,channel,direction,content,metadata,occurred_at)
                            VALUES (%s,%s,'email_reply','email','inbound','QA: Please send details',%s::jsonb,NOW()-INTERVAL '10 days')""",
                            (contact,uid,json.dumps({'outbound_message_id':mid,'reply_classification':{'label':'positive_soft','positive':True}})))
        role = conn.execute('SELECT current_user').fetchone()['current_user']
    print(json.dumps({'migrations_applied': len(applied), 'runtime_role': role, 'url': URL}))


def browser_test():
    from playwright.sync_api import sync_playwright, expect
    out = ROOT / 'artifacts' / 'client-acceptance'
    out.mkdir(parents=True, exist_ok=True)
    errors, failures = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for username, expected_sent in [('qa_sales', 3), ('qa_other', 1), ('qa_admin', 4)]:
            context = browser.new_context(viewport={'width':1440,'height':1000})
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('response', lambda response: failures.append([response.url,response.status]) if response.status >= 500 else None)
            page.goto(URL)
            page.get_by_label('账号', exact=True).fill(username)
            page.get_by_label('密码', exact=True).fill(PASSWORD)
            page.get_by_role('button', name='进入工作台', exact=True).click()
            expect(page.locator('#login-screen')).to_be_hidden()
            page.goto(URL + '/#outreach')
            page.get_by_role('button', name='已发送邮件', exact=True).click()
            panel = page.get_by_role('region', name='邮件效果复盘')
            try:
                expect(panel.get_by_text('已有正向回复', exact=True)).to_be_visible()
            except AssertionError:
                page.screenshot(path=str(out / 'failure.png'), full_page=True)
                print(json.dumps({'errors':errors,'failures':failures,'page':page.locator('body').inner_text()[-3500:]},ensure_ascii=False))
                raise
            expect(page).to_have_url(URL + '/#sent-emails')
            page.reload()
            expect(panel.get_by_text('已有正向回复', exact=True)).to_be_visible()
            expect(page.get_by_role('button', name='已发送邮件', exact=True)).to_have_attribute('aria-pressed', 'true')
            page.wait_for_function('navigator.serviceWorker.controller !== null')
            for days in (7,14,30):
                response = context.request.get(f'{URL}/api/email-performance?days={days}')
                assert response.status == 200
                data = response.json()['data']
                assert sum(row['sent'] for row in data['weeks']) == expected_sent
                assert data['scope'] == ('all' if username == 'qa_admin' else 'current_owner')
            tampered = context.request.get(URL+'/api/email-performance?days=14&user_id=1').json()['data']
            assert sum(row['sent'] for row in tampered['weeks']) == expected_sent
            assert context.request.get(URL+'/api/admin/users').status == (200 if username == 'qa_admin' else 403)
            if username == 'qa_sales':
                for width,height in [(1440,1000),(390,844)]:
                    page.set_viewport_size({'width':width,'height':height})
                    panel.scroll_into_view_if_needed()
                    expect(panel).to_be_visible()
                    assert panel.bounding_box()['width'] >= width * .6
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                    page.evaluate('window.scrollTo(0,0)')
                    page.screenshot(path=str(out / f'authenticated-performance-{width}.png'), full_page=True)
                page.get_by_label('邮件效果观察期').select_option('30')
                expect(panel.get_by_text('已有正向回复', exact=True)).to_have_count(0)
                expect(panel.get_by_text('观察中',exact=True).first).to_be_visible()
                panel.get_by_role('link', name='处理客户跟进').first.click()
                expect(page).to_have_url(URL+'/#followup')
            context.request.get(URL+'/api/logout')
            assert context.request.get(URL+'/api/email-performance').status == 401
            context.close()
        assert not errors, errors
        assert not failures, failures
        browser.close()
    print(json.dumps({'result':'PASS','real_logins':3,'runtime_RLS':True,'mocked_requests':0,
                      'route_reload':'PASS','PWA_controlled':True,'browser_errors':errors,'server_errors':failures}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['setup','serve','test'])
    mode = parser.parse_args().mode
    if mode == 'test':
        browser_test()
    else:
        config, repo = repository()
        if mode == 'setup':
            setup(repo)
        else:
            ThreadingHTTPServer(('127.0.0.1',18773), make_handler(config,repo)).serve_forever()
