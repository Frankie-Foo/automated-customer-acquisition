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

const sabcdStages = [
  ["D", "D 未接触", "还没有触达，适合继续找邮箱和准备首封邮件"],
  ["C", "C 已触达", "已经发过邮件或发生触达，等待打开、回复或二次触达"],
  ["B", "B 多轮沟通", "客户有回复或进入销售沟通，需要人工推进"],
  ["A", "A 商业计划/试订单", "已讨论计划、试订单或代理协议，重点跟进风险和资料"],
  ["S", "S 签约建店", "已签约或建店，进入持续维护"],
];

export default function DashboardViewsPortal() {
  const [targets, setTargets] = useState({});
  const [readinessTarget, setReadinessTarget] = useState(null);
  const [user, setUser] = useState(null);

  useEffect(() => {
    document.body.classList.add("react-dashboard-enabled");
    setTargets({
      dashboard: document.querySelector("#react-dashboard-root"),
      agentMap: document.querySelector("#react-agent-map-root"),
      quickstart: document.querySelector("#react-quickstart-root"),
      ops: document.querySelector("#react-ops-root"),
      followups: document.querySelector("#react-followups-root"),
      lifecycle: document.querySelector("#react-lifecycle-root"),
    });
    setReadinessTarget(document.querySelector("#react-readiness-root"));
    return () => document.body.classList.remove("react-dashboard-enabled");
  }, []);

  useEffect(() => {
    const handleSession = (event) => setUser(event.detail?.user || null);
    window.addEventListener("salesbot:session", handleSession);
    api("/api/me").then((session) => setUser(session.user)).catch(() => setUser(null));
    return () => window.removeEventListener("salesbot:session", handleSession);
  }, []);

  return (
    <>
      <DashboardViews targets={targets} />
      {readinessTarget && user?.role === "admin" && createPortal(<ReadinessPanel />, readinessTarget)}
    </>
  );
}

function DashboardViews({ targets }) {
  const [user, setUser] = useState(null);
  const [summary, setSummary] = useState(null);
  const [ops, setOps] = useState(null);
  const [lifecycle, setLifecycle] = useState(null);
  const [contacts, setContacts] = useState([]);

  const load = useCallback(async () => {
    if (!user) return;
    const [summaryData, opsData, lifecycleData, contactsData] = await Promise.all([
      api("/api/summary"),
      api("/api/ops-report"),
      api("/api/lifecycle"),
      api("/api/contacts?limit=100"),
    ]);
    setSummary(summaryData);
    setOps(opsData);
    setLifecycle(lifecycleData);
    setContacts(contactsData.contacts || []);
  }, [user]);

  useEffect(() => {
    const session = (event) => setUser(event.detail?.user || null);
    const refresh = () => load().catch(() => {});
    window.addEventListener("salesbot:session", session);
    window.addEventListener("salesbot:refresh-related", refresh);
    window.addEventListener("salesbot:ops-refresh", refresh);
    return () => {
      window.removeEventListener("salesbot:session", session);
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
      {targets.dashboard && createPortal(<Metrics summary={summary || {}} />, targets.dashboard)}
      {targets.agentMap && createPortal(<AgentMap />, targets.agentMap)}
      {targets.quickstart && createPortal(<QuickStart user={user} />, targets.quickstart)}
      {targets.ops && createPortal(<OpsReport report={ops || {}} user={user} />, targets.ops)}
      {targets.followups && createPortal(<Followups contacts={contacts} />, targets.followups)}
      {targets.lifecycle && createPortal(<Lifecycle lifecycle={lifecycle || {}} contacts={contacts} />, targets.lifecycle)}
    </>
  );
}

function AgentMap() {
  const agents = [
    ["市场情报员", "产品、国家、客户类型", "市场方向、关键词、渠道"],
    ["客户搜索员", "关键词、渠道、公司种子", "公司名单、联系人、官网来源"],
    ["公司背调员", "官网、产品页、社媒资料", "客户画像、匹配度、风险点"],
    ["开发信助理", "客户画像、产品卖点", "首封开发信、跟进邮件"],
    ["跟进提醒员", "客户阶段、邮件反馈", "跟进时间、下一步动作"],
    ["主管周报员", "客户表、跟进表、邮件回流", "周报、重点客户提醒"],
  ];
  return (
    <section className="agent-map">
      <div className="followup-head">
        <div>
          <span className="eyebrow">AI workforce</span>
          <h2>6 个 AI 员工协同获客</h2>
        </div>
        <p>每个模块只负责一件事，销售按页面顺序推进，管理员看结果和风险。</p>
      </div>
      <div className="agent-grid">
        {agents.map(([name, input, output], index) => (
          <article className="agent-card" key={name}>
            <b>{index + 1}</b>
            <strong>{name}</strong>
            <span>输入：{input}</span>
            <em>输出：{output}</em>
          </article>
        ))}
      </div>
    </section>
  );
}

function QuickStart({ user }) {
  const isAdmin = user?.role === "admin";
  const items = isAdmin
    ? [
        ["账号与权限", "创建销售账号、设置每日获客/发信配额，确认销售只能看自己的客户。", "#admin", "去管理员控制台"],
        ["导入与分配", "批量导入公司/门店表，获客结果先进入客户池，再按销售或地区分配。", "#lifecycle", "开始获客"],
        ["发信与回流", "检查发件域名、发件池和邮件中心，确认送达、打开、退信回流正常。", "#emails", "查看邮件"],
        ["复盘与优化", "每天看有效邮箱、发送、打开、回复、退信，调整数据源和邮件话术。", "#dashboard", "看日报"],
      ]
    : [
        ["今天先看待办", "优先处理已打开未回复、已回复、退信客户，避免客户卡在无人跟进。", "#lifecycle", "去跟进"],
        ["导入客户来源", "上传公司/门店表，系统会找负责人、补邮箱，并把结果放进你的客户列表。", "#lifecycle", "导入线索"],
        ["确认再发邮件", "有 valid 工作邮箱后加入队列，发送前可用 AI 生成或手动修改邮件内容。", "#lifecycle", "准备发信"],
        ["推进 SABCD", "每次沟通后更新 D/C/B/A/S 阶段，系统会沉淀客户画像和下一步建议。", "#lifecycle", "更新阶段"],
      ];
  return (
    <section className="quickstart">
      <div className="quickstart-head">
        <div>
          <span className="eyebrow">{isAdmin ? "Admin workflow" : "Daily workflow"}</span>
          <h2>{isAdmin ? "管理员上线工作台" : "今日操作路径"}</h2>
        </div>
        <p>{isAdmin ? "先保证账号、配额、发件和数据回流可控，再放量给团队使用。" : "按照这四步走，从找客户到发邮件再到销售跟进闭环。"}</p>
      </div>
      <div className="quickstart-grid">
        {items.map(([title, text, href, cta], index) => (
          <a className="quickstart-card" href={href} key={title}>
            <b>{index + 1}</b>
            <strong>{title}</strong>
            <span>{text}</span>
            <em>{cta}</em>
          </a>
        ))}
      </div>
    </section>
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
      <div className="readiness" id="readiness">
        {(data?.checks || []).map((check) => <div key={check.name} className={`check ${check.ok ? "ok" : "missing"}`} title={check.message || ""}><span>{readinessLabel(check.name)}</span><strong>{check.ok ? "OK" : (check.required ? "缺失" : "可选")}</strong></div>)}
      </div>
    </>
  );
}

function Metrics({ summary }) {
  const events = summary.events_7d || {};
  const sabcd = summary.sabcd || {};
  const cards = [
    ["客户总数", summary.total_contacts || 0, "当前可见客户"],
    ["今日发送", summary.sent_today || 0, "当天真实/演练发送"],
    ["待发送", summary.statuses?.queued || 0, "已入队等待触达"],
    ["7天打开", events.opened || 0, "最近 7 天打开事件"],
    ["已回复", summary.statuses?.replied || 0, "需要销售跟进"],
    ["A/S 客户", Number(sabcd.A || 0) + Number(sabcd.S || 0), "商业计划、试订单、签约建店"],
  ];
  return <section className="metrics">{cards.map(([label, value, hint]) => <div className="metric" key={label}><span>{label}</span><strong>{value}</strong><small>{hint}</small></div>)}</section>;
}

function OpsReport({ report, user }) {
  const totals = report.totals || {};
  const events = report.events || {};
  const isTeam = (report.scope || "") === "team" || user.role === "admin";
  return (
    <section className="ops-report" id="ops-report">
      <div className="followup-head">
        <div><span className="eyebrow">{isTeam ? "Team operations" : "My operations"}</span><h2>{isTeam ? "团队运营与日报" : "我的今日数据"}</h2></div>
        <p>{isTeam ? "管理员查看团队获客、发信和回流表现。" : "这里只显示你自己的获客、发信和回流表现。"}</p>
      </div>
      <div className="ops-cards">
        <OpsCard label="今日新增线索" value={totals.new_contacts_today} />
        <OpsCard label="今日有效邮箱" value={totals.valid_emails_today} />
        <OpsCard label="今日发送" value={events.sent_today} />
        <OpsCard label="今日送达" value={events.delivered_today} />
        <OpsCard label="今日打开" value={events.opened_today} />
        <OpsCard label="今日回复" value={(totals.replied || 0) + (events.replied_events_today || 0)} />
        <OpsCard label="今日退信" value={(totals.bounced || 0) + (events.bounced_events_today || 0)} />
        <OpsCard label="今日需处理" value={(events.opened_no_reply || 0) + (totals.replied || 0) + (totals.bounced || 0)} />
      </div>
      <div className="ops-grid">
        <section>
          <h3>{isTeam ? "销售配额日报" : "我的配额"}</h3>
          <table className="mini-table">
            <thead><tr><th>销售</th><th>获客</th><th>发信</th><th>客户</th><th>状态</th></tr></thead>
            <tbody>{(report.by_user || []).map((row) => <tr key={row.id}><td>{row.display_name || row.username}</td><td>{row.source_count_today || 0}/{row.daily_source_limit}</td><td>{row.send_count_today || 0}/{row.daily_send_limit}</td><td>{row.owned_contacts || 0}</td><td>{row.active ? "启用" : "停用"}</td></tr>)}</tbody>
          </table>
        </section>
        {isTeam && <section>
          <h3>邮箱 Provider 统计</h3>
          <table className="mini-table">
            <thead><tr><th>Provider</th><th>调用</th><th>候选</th><th>Valid</th><th>选中</th><th>错误</th></tr></thead>
            <tbody>{(report.provider_stats || []).slice(0, 8).map((row) => <tr key={`${row.provider}-${row.stat_date}`}><td>{row.provider}</td><td>{row.calls || 0}</td><td>{row.candidates || 0}</td><td>{row.valid_candidates || 0}</td><td>{row.selected || 0}</td><td>{row.errors || 0}</td></tr>)}</tbody>
          </table>
        </section>}
        <section>
          <h3>失败原因</h3>
          <ul className="failure-list">{(report.failures || []).length ? (report.failures || []).slice(0, 6).map((item) => <li key={item.reason}><span>{item.reason}</span><b>{item.count}</b></li>) : <li><span>暂无失败</span><b>0</b></li>}</ul>
        </section>
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
  return <section className="followups" id="followups"><div className="followup-head"><div><span className="eyebrow">Sales follow-up</span><h2>今日待办</h2></div><p>优先处理已打开未回复、已回复和退信客户。</p></div><div className="followup-grid"><FollowupCard title="已打开未回复" hint="建议今天人工跟进或准备下一封" tone="hot" contacts={openedNoReply} /><FollowupCard title="已回复" hint="需要销售马上接手沟通" tone="reply" contacts={replied} /><FollowupCard title="退信需处理" hint="检查邮箱质量或加入黑名单" tone="risk" contacts={bounced} /></div></section>;
}

function FollowupCard({ title, hint, tone, contacts }) {
  return <article className={`followup-card ${tone}`}><div className="followup-title"><div><strong>{title}</strong><span>{hint}</span></div><b>{contacts.length}</b></div><ul>{contacts.slice(0, 3).map((contact) => <li key={contact.id}><div><strong>{fullName(contact)}</strong><span>{contact.company_name || contact.company_domain || ""}</span></div><em>{followupMeta(contact)}</em></li>)}{!contacts.length && <li className="empty-task">当前没有需要处理的客户</li>}</ul></article>;
}

function Lifecycle({ lifecycle, contacts }) {
  const stages = lifecycle.stages || {};
  const sabcd = lifecycle.sabcd || {};
  return <section className="lifecycle-board" id="lifecycle-board"><div className="followup-head"><div><span className="eyebrow">Customer lifecycle</span><h2>客户生命周期漏斗</h2></div><p>用 SABCD 管理客户成熟度，用生命周期管理每一步销售动作。</p></div><div className="sabcd-grid">{sabcdStages.map(([key, label, hint]) => { const examples = contacts.filter((c) => (c.sabcd_stage || "D") === key).slice(0, 2); return <article key={key} className={`sabcd-card sabcd-${key.toLowerCase()}`}><div><strong>{label}</strong><span>{hint}</span></div><b>{sabcd[key] || 0}</b><small>{examples.length ? examples.map((c) => <em key={c.id}>{fullName(c)}</em>) : <em>暂无客户</em>}</small></article>; })}</div><div className="lifecycle-grid">{lifecycleStages.map(([key, label]) => { const examples = contacts.filter((c) => c.lifecycle_stage === key).slice(0, 2); return <article key={key} className={`lifecycle-card ${key}`}><strong>{label}</strong><b>{stages[key] || 0}</b><div>{examples.length ? examples.map((c) => <span key={c.id}>{fullName(c)}</span>) : <span>暂无客户</span>}</div></article>; })}</div></section>;
}

function fullName(contact) {
  return [contact.first_name, contact.last_name].filter(Boolean).join(" ") || "(No name)";
}

function followupMeta(contact) {
  if (contact.status === "replied" || Number(contact.replied_count || 0) > 0) return "已回复";
  if (contact.status === "bounced" || Number(contact.bounced_count || 0) > 0) return "退信";
  if (Number(contact.opened_count || 0) > 0) return `打开 ${contact.opened_count} 次`;
  if (Number(contact.delivered_count || 0) > 0) return `送达 ${contact.delivered_count} 次`;
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
    quotas: "配额配置",
  }[name] || name;
}
