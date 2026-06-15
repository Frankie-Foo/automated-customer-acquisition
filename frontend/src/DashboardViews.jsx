import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "./api.js";

const lifecycleStages = [
  ["lead", "陌生线索"],
  ["replied", "已回复"],
  ["conversation", "初步沟通"],
  ["meeting", "约会/会议"],
  ["business_plan", "商业计划"],
  ["store_visit", "到店参观"],
  ["trial_order", "试订单"],
  ["agency_agreement", "代理协议"],
  ["hq_visit", "总部拜访"],
  ["signed", "成功签约"],
  ["maintenance", "持续维护"],
  ["waiting_pool", "等待池"],
  ["abandoned", "已放弃"],
];

export default function DashboardViewsPortal() {
  const [dashboardTarget, setDashboardTarget] = useState(null);
  const [readinessTarget, setReadinessTarget] = useState(null);

  useEffect(() => {
    document.body.classList.add("react-dashboard-enabled");
    setDashboardTarget(document.querySelector("#react-dashboard-root"));
    setReadinessTarget(document.querySelector("#react-readiness-root"));
    return () => document.body.classList.remove("react-dashboard-enabled");
  }, []);

  return (
    <>
      {dashboardTarget && createPortal(<DashboardViews />, dashboardTarget)}
      {readinessTarget && createPortal(<ReadinessPanel />, readinessTarget)}
    </>
  );
}

function DashboardViews() {
  const [user, setUser] = useState(null);
  const [summary, setSummary] = useState(null);
  const [ops, setOps] = useState(null);
  const [lifecycle, setLifecycle] = useState(null);
  const [contacts, setContacts] = useState([]);

  const load = useCallback(async () => {
    if (!user) return;
    const [summaryData, opsData, lifecycleData] = await Promise.all([
      api("/api/summary"),
      api("/api/ops-report"),
      api("/api/lifecycle"),
    ]);
    setSummary(summaryData);
    setOps(opsData);
    setLifecycle(lifecycleData);
  }, [user]);

  useEffect(() => {
    const session = (event) => setUser(event.detail?.user || null);
    const contactsUpdated = (event) => setContacts(event.detail?.contacts || []);
    const refresh = () => load().catch(() => {});
    window.addEventListener("salesbot:session", session);
    window.addEventListener("salesbot:contacts-updated", contactsUpdated);
    window.addEventListener("salesbot:refresh-related", refresh);
    window.addEventListener("salesbot:ops-refresh", refresh);
    return () => {
      window.removeEventListener("salesbot:session", session);
      window.removeEventListener("salesbot:contacts-updated", contactsUpdated);
      window.removeEventListener("salesbot:refresh-related", refresh);
      window.removeEventListener("salesbot:ops-refresh", refresh);
    };
  }, [load]);

  useEffect(() => {
    api("/api/me").then((session) => setUser(session.user)).catch(() => setUser(null));
  }, []);

  useEffect(() => {
    load().catch(() => {});
  }, [load]);

  if (!user) return null;

  return (
    <>
      <Metrics summary={summary || {}} />
      <OpsReport report={ops || {}} user={user} />
      <Followups contacts={contacts} />
      <Lifecycle lifecycle={lifecycle || {}} contacts={contacts} />
    </>
  );
}

function ReadinessPanel() {
  const [data, setData] = useState(null);

  const load = useCallback(() => {
    api("/api/readiness").then(setData).catch(() => setData({ ready: false, checks: [{ name: "readiness", ok: false, required: true }] }));
  }, []);

  useEffect(() => {
    load();
    window.addEventListener("salesbot:refresh-related", load);
    return () => window.removeEventListener("salesbot:refresh-related", load);
  }, [load]);

  return (
    <>
      <div className="side-heading"><h2>生产状态</h2><span className={data?.ready ? "ready" : "missing"}>{data?.ready ? "Ready" : "Action needed"}</span></div>
      <div className="readiness">
        {(data?.checks || []).map((check) => <div key={check.name} className={`check ${check.ok ? "ok" : "missing"}`} title={check.message || ""}><span>{readinessLabel(check.name)}</span><strong>{check.ok ? "OK" : (check.required ? "缺失" : "可选")}</strong></div>)}
      </div>
    </>
  );
}

function Metrics({ summary }) {
  const events = summary.events_7d || {};
  const cards = [
    ["客户总数", summary.total_contacts || 0, "系统内全部客户"],
    ["今日发送", summary.sent_today || 0, "当天真实/演练发送"],
    ["待发送", summary.statuses?.queued || 0, "已入队等待触达"],
    ["7天打开", events.opened || 0, "最近 7 天打开事件"],
    ["已回复", summary.statuses?.replied || 0, "需要销售跟进"],
  ];
  return <section className="metrics">{cards.map(([label, value, hint]) => <div className="metric" key={label}><span>{label}</span><strong>{value}</strong><small>{hint}</small></div>)}</section>;
}

function OpsReport({ report, user }) {
  const totals = report.totals || {};
  const events = report.events || {};
  const isTeam = (report.scope || "") === "team" || user.role === "admin";
  return (
    <section className="ops-report">
      <div className="followup-head"><div><span className="eyebrow">Team operations</span><h2>团队运营与日报</h2></div><p>查看今日获客、有效邮箱、发送、打开、回复、退信和各销售配额使用情况。</p></div>
      <div className="ops-cards">
        <OpsCard label="今日新增线索" value={totals.new_contacts_today} />
        <OpsCard label="今日有效邮箱" value={totals.valid_emails_today} />
        <OpsCard label="今日发送" value={events.sent_today} />
        <OpsCard label="今日打开" value={events.opened_today} />
        <OpsCard label="今日回复" value={(totals.replied || 0) + (events.replied_events_today || 0)} />
        <OpsCard label="今日退信" value={(totals.bounced || 0) + (events.bounced_events_today || 0)} />
        <OpsCard label="今日需处理" value={(events.opened_no_reply || 0) + (totals.replied || 0) + (totals.bounced || 0)} />
      </div>
      <div className="ops-grid">
        <section><h3>销售配额日报</h3><table className="mini-table"><thead><tr><th>销售</th><th>获客</th><th>发信</th><th>客户</th><th>状态</th></tr></thead><tbody>{(report.by_user || []).map((row) => <tr key={row.id}><td>{row.display_name || row.username}</td><td>{row.source_count_today || 0}/{row.daily_source_limit}</td><td>{row.send_count_today || 0}/{row.daily_send_limit}</td><td>{row.owned_contacts || 0}</td><td>{row.active ? "启用" : "停用"}</td></tr>)}</tbody></table></section>
        {isTeam && <section><h3>邮箱 Provider 统计</h3><table className="mini-table"><thead><tr><th>Provider</th><th>调用</th><th>候选</th><th>Valid</th><th>选中</th><th>错误</th></tr></thead><tbody>{(report.provider_stats || []).slice(0, 8).map((row) => <tr key={`${row.provider}-${row.stat_date}`}><td>{row.provider}</td><td>{row.calls || 0}</td><td>{row.candidates || 0}</td><td>{row.valid_candidates || 0}</td><td>{row.selected || 0}</td><td>{row.errors || 0}</td></tr>)}</tbody></table></section>}
        <section><h3>失败原因</h3><ul className="failure-list">{(report.failures || []).slice(0, 6).map((item) => <li key={item.reason}><span>{item.reason}</span><b>{item.count}</b></li>) || <li><span>暂无失败</span><b>0</b></li>}</ul></section>
      </div>
    </section>
  );
}

function OpsCard({ label, value }) {
  return <article><span>{label}</span><strong>{Number(value || 0)}</strong></article>;
}

function Followups({ contacts }) {
  const openedNoReply = contacts.filter((c) => Number(c.opened_count || 0) > 0 && !["replied", "bounced", "unsubscribed"].includes(c.status));
  const replied = contacts.filter((c) => c.status === "replied" || Number(c.replied_count || 0) > 0);
  const bounced = contacts.filter((c) => c.status === "bounced" || Number(c.bounced_count || 0) > 0);
  return <section className="followups"><div className="followup-head"><div><span className="eyebrow">Sales follow-up</span><h2>今日待办</h2></div><p>优先处理已打开未回复、已回复和退信客户。</p></div><div className="followup-grid"><FollowupCard title="已打开未回复" hint="建议今天人工跟进或准备下一封" tone="hot" contacts={openedNoReply} /><FollowupCard title="已回复" hint="需要销售马上接手沟通" tone="reply" contacts={replied} /><FollowupCard title="退信需处理" hint="检查邮箱质量或加入黑名单" tone="risk" contacts={bounced} /></div></section>;
}

function FollowupCard({ title, hint, tone, contacts }) {
  return <article className={`followup-card ${tone}`}><div className="followup-title"><div><strong>{title}</strong><span>{hint}</span></div><b>{contacts.length}</b></div><ul>{contacts.slice(0, 3).map((contact) => <li key={contact.id}><div><strong>{fullName(contact)}</strong><span>{contact.company_name || contact.company_domain || ""}</span></div><em>{followupMeta(contact)}</em></li>)}{!contacts.length && <li className="empty-task">当前没有需要处理的客户</li>}</ul></article>;
}

function Lifecycle({ lifecycle, contacts }) {
  const stages = lifecycle.stages || {};
  return <section className="lifecycle-board"><div className="followup-head"><div><span className="eyebrow">Customer lifecycle</span><h2>客户生命周期漏斗</h2></div><p>从回复、沟通、约会、试订单到签约维护，持续沉淀客户画像。</p></div><div className="lifecycle-grid">{lifecycleStages.map(([key, label]) => { const examples = contacts.filter((c) => c.lifecycle_stage === key).slice(0, 2); return <article key={key} className={`lifecycle-card ${key}`}><strong>{label}</strong><b>{stages[key] || 0}</b><div>{examples.length ? examples.map((c) => <span key={c.id}>{fullName(c)}</span>) : <span>暂无客户</span>}</div></article>; })}</div></section>;
}

function fullName(contact) {
  return [contact.first_name, contact.last_name].filter(Boolean).join(" ") || "(No name)";
}

function followupMeta(contact) {
  if (contact.status === "replied" || Number(contact.replied_count || 0) > 0) return "已回复";
  if (contact.status === "bounced" || Number(contact.bounced_count || 0) > 0) return "退信";
  if (Number(contact.opened_count || 0) > 0) return `打开 ${contact.opened_count} 次`;
  return eventLabel(contact.last_event_type || contact.status);
}

function eventLabel(type) {
  return { sent: "已发送", opened: "已打开", clicked: "已点击", replied: "已回复", bounced: "已退信", unsubscribed: "已退订" }[type] || type;
}

function readinessLabel(name) {
  return {
    database: "数据库连接",
    lead_source: "自动获客 API",
    enrichment: "邮箱富化 API",
    resend: "邮件发送 API",
    sender_email: "发件邮箱域名",
    dry_run: "真实发送开关",
    public_url: "公网访问地址",
    admin_password: "管理员密码",
    social_enrichment: "社媒富化 API",
    llm: "AI 文案模型",
    slack: "Slack 通知",
  }[name] || name;
}
