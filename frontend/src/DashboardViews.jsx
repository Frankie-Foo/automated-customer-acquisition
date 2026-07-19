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
  ["D", "D 未接触", "还没触达，适合继续找邮箱和准备首封邮件"],
  ["C", "C 已触达", "已发邮件或发生触达，等待打开、回复或二次触达"],
  ["B", "B 多轮沟通", "客户有回复或进入销售沟通，需要人工推进"],
  ["A", "A 商业计划/试订单", "已讨论计划、试订单或代理协议，重点跟进风险和资料"],
  ["S", "S 签约建店", "已签约或建店，进入持续维护和画像沉淀"],
];

export default function DashboardViewsPortal({ activePage = "dashboard" }) {
  const [targets, setTargets] = useState({});
  const [readinessTarget, setReadinessTarget] = useState(null);
  const [user, setUser] = useState(() => window.SALESBOT_SESSION?.user || null);

  useEffect(() => {
    document.body.classList.add("react-dashboard-enabled");
    setTargets({
      dashboard: document.querySelector("#react-dashboard-root"),
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
    return () => window.removeEventListener("salesbot:session", handleSession);
  }, []);

  return (
    <>
      <DashboardViews targets={targets} activePage={activePage} />
      {readinessTarget && user?.role === "admin" && createPortal(<ReadinessPanel />, readinessTarget)}
    </>
  );
}

function DashboardViews({ targets, activePage }) {
  const [user, setUser] = useState(() => window.SALESBOT_SESSION?.user || null);
  const [summary, setSummary] = useState(null);
  const [ops, setOps] = useState(null);
  const [lifecycle, setLifecycle] = useState(null);
  const [contacts, setContacts] = useState([]);
  const [publicContacts, setPublicContacts] = useState([]);
  const [tasks, setTasks] = useState([]);

  const load = useCallback(async () => {
    if (!user) return;
    if (activePage === "dashboard") {
      const [summaryData, contactsData, publicData] = await Promise.all([
        api("/api/summary"),
        api(user.role === "admin" ? "/api/contacts?limit=100" : "/api/contacts?limit=100&filter=private_pool"),
        user.role === "admin" ? Promise.resolve({ contacts: [] }) : api("/api/contacts?limit=100&filter=public_pool"),
      ]);
      setSummary(summaryData);
      setContacts(contactsData.contacts || []);
      setPublicContacts(publicData.contacts || []);
      return;
    }
    if (activePage === "followup") {
      const [lifecycleData, contactsData, taskData] = await Promise.all([
        api("/api/lifecycle"),
        api(user.role === "admin" ? "/api/contacts?limit=100" : "/api/contacts?limit=100&filter=private_pool"),
        api("/api/followup-tasks?status=open&limit=100"),
      ]);
      setLifecycle(lifecycleData);
      setContacts(contactsData.contacts || []);
      setTasks(taskData.tasks || []);
      return;
    }
    if (activePage === "report") setOps(await api("/api/ops-report"));
  }, [activePage, user]);

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
    load().catch(() => {});
  }, [load]);

  if (!user) return null;

  return (
    <>
      {targets.dashboard && activePage === "dashboard" && createPortal(<Metrics summary={summary || {}} />, targets.dashboard)}
      {targets.quickstart && activePage === "dashboard" && createPortal(<QuickStart user={user} contacts={contacts} publicContacts={publicContacts} />, targets.quickstart)}
      {targets.ops && activePage === "report" && createPortal(<OpsReport report={ops || {}} user={user} />, targets.ops)}
      {targets.followups && activePage === "followup" && createPortal(<Followups contacts={contacts} tasks={tasks} reload={load} />, targets.followups)}
      {targets.lifecycle && activePage === "followup" && createPortal(<Lifecycle lifecycle={lifecycle || {}} contacts={contacts} />, targets.lifecycle)}
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
  return (
    <section className="metrics">
      {cards.map(([label, value, hint]) => (
        <div className="metric" key={label}>
          <span>{label}</span>
          <strong>{value}</strong>
          <small>{hint}</small>
        </div>
      ))}
    </section>
  );
}

function QuickStart({ user, contacts, publicContacts }) {
  const isAdmin = user?.role === "admin";
  const items = isAdmin
    ? [
        { title: "系统状态", href: "#admin", hint: "检查生产配置与异常", action: "检查" },
        { title: "账号与配额", href: "#admin", hint: "管理销售账号和每日额度", action: "管理" },
        { title: "团队数据", href: "#report", hint: "查看获客、发送和回复表现", action: "查看" },
        { title: "邮件抽查", href: "#outreach", hint: "抽查发送内容与回流", action: "抽查" },
      ]
    : salesActions(contacts, publicContacts);
  return (
    <section className="quickstart action-center">
      <div className="quickstart-head">
        <div>
          <span className="eyebrow">{isAdmin ? "ADMIN" : "TODAY"}</span>
          <h2>{isAdmin ? "管理入口" : "下一步做什么"}</h2>
          {!isAdmin && <p>按优先级处理客户。完成当前动作后，系统会自动给出下一步。</p>}
        </div>
      </div>
      <div className="quickstart-grid">
        {items.map((item, index) => (
          <a className={`quickstart-card ${item.priority ? "recommended" : ""}`} href={item.href} key={item.title}>
            <b>{index + 1}</b>
            <span className="quickstart-copy">
              <strong>{item.title}</strong>
              <span>{item.hint}</span>
            </span>
            <em>{item.count != null ? `${item.count} 个` : item.action}</em>
          </a>
        ))}
      </div>
    </section>
  );
}

function salesActions(contacts, publicContacts) {
  const own = (contacts || []).filter((contact) => contact.pool_type !== "public");
  const followups = own.filter((contact) => contact.status === "replied"
    || Number(contact.replied_count || 0) > 0
    || Number(contact.opened_count || 0) > 0
    || contact.status === "bounced");
  const needsEmail = own.filter((contact) => !contact.email || contact.email_status !== "valid");
  const ready = own.filter((contact) => contact.email_status === "valid"
    && ["new", "enriched", "queued"].includes(contact.status || "new"));
  const actions = [
    { title: "先处理客户反馈", href: "#followup", hint: "回复、已打开未回复和退信", count: followups.length, rank: followups.length ? 0 : 4 },
    { title: "领取公共客户", href: "#research", hint: "查看资料后领取到私人客户池", count: publicContacts.length, rank: publicContacts.length ? 1 : 5 },
    { title: "补齐客户资料", href: "#research", hint: "核验身份、邮箱和客户画像", count: needsEmail.length, rank: needsEmail.length ? 2 : 6 },
    { title: "准备个性化邮件", href: "#research", hint: "选择客户，检查草稿后再发送", count: ready.length, rank: ready.length ? 3 : 7 },
  ].sort((left, right) => left.rank - right.rank);
  return actions.map((item, index) => ({ ...item, priority: index === 0 && item.count > 0 }));
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
      <div className="side-heading">
        <h2>生产状态</h2>
        <span className={data?.ready ? "ready" : "missing"}>{data?.ready ? "Ready" : "Action needed"}</span>
      </div>
      <div className="readiness" id="readiness">
        {(data?.checks || []).map((check) => (
          <div key={check.name} className={`check ${check.ok ? "ok" : "missing"}`} title={check.message || ""}>
            <span>{readinessLabel(check.name)}</span>
            <strong>{check.ok ? "OK" : check.required ? "缺失" : "可选"}</strong>
          </div>
        ))}
      </div>
    </>
  );
}

function OpsReport({ report, user }) {
  const totals = report.totals || {};
  const events = report.events || {};
  const funnel = report.funnel || {};
  const blockers = report.blockers || {};
  const isTeam = (report.scope || "") === "team" || user.role === "admin";
  return (
    <section className="ops-report" id="ops-report">
      <div className="followup-head">
        <div>
          <span className="eyebrow">{isTeam ? "Team operations" : "My operations"}</span>
          <h2>{isTeam ? "团队运营与周报" : "我的今日数据"}</h2>
        </div>
        <p>{isTeam ? "管理员查看团队获客、发信、回流和数据源表现。" : "这里只显示你自己的获客、发信和回流表现。"}</p>
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
      <ConversionFunnel funnel={funnel} />
      <PipelineBlockers blockers={blockers} />
      <div className="ops-grid">
        <section>
          <h3>{isTeam ? "销售配额日报" : "我的配额"}</h3>
          <table className="mini-table">
            <thead><tr><th>销售</th><th>获客</th><th>发信</th><th>客户</th><th>状态</th></tr></thead>
            <tbody>{(report.by_user || []).map((row) => <tr key={row.id}><td>{row.display_name || row.username}</td><td>{row.source_count_today || 0}/{row.daily_source_limit}</td><td>{row.send_count_today || 0}/{row.daily_send_limit}</td><td>{row.owned_contacts || 0}</td><td>{row.active ? "启用" : "停用"}</td></tr>)}</tbody>
          </table>
        </section>
        {isTeam && <ProviderStats rows={report.provider_stats || []} />}
        <section>
          <h3>失败原因</h3>
          <ul className="failure-list">{(report.failures || []).length ? (report.failures || []).slice(0, 6).map((item) => <li key={item.reason}><span>{item.reason}</span><b>{item.count}</b></li>) : <li><span>暂无失败</span><b>0</b></li>}</ul>
        </section>
      </div>
    </section>
  );
}

function ConversionFunnel({ funnel }) {
  const stages = [
    ["线索", "leads"],
    ["已分配", "private_pool"],
    ["有效邮箱", "valid_email"],
    ["客户画像", "profiled"],
    ["邮件草稿", "drafted"],
    ["已审核", "approved"],
    ["已发送", "sent"],
    ["已打开", "opened"],
    ["已回复", "replied"],
    ["B/A/S", "qualified"],
    ["已签约", "signed"],
  ];
  const base = Math.max(1, Number(funnel.leads || 0));
  return <section className="conversion-funnel">
    <div className="section-title-row"><div><span className="eyebrow">Conversion funnel</span><h3>客户转化漏斗</h3></div><p>从获客到签约的累计客户数</p></div>
    <div className="funnel-steps">{stages.map(([label, key], index) => {
      const value = Number(funnel[key] || 0);
      const previous = index ? Number(funnel[stages[index - 1][1]] || 0) : value;
      const rate = index && previous > 0 ? Math.round((value / previous) * 100) : null;
      const rateLabel = !index
        ? "全部线索"
        : rate === null
          ? "暂无上一步基线"
          : rate > 100
            ? "历史数据未完整回填"
            : `上一步转化 ${rate}%`;
      return <div className="funnel-step" key={key}><div><strong>{label}</strong><b>{value}</b></div><span><i style={{ width: `${Math.max(4, Math.round((value / base) * 100))}%` }} /></span><small>{rateLabel}</small></div>;
    })}</div>
  </section>;
}

function PipelineBlockers({ blockers }) {
  const items = [
    ["公共池待分配", "public_unassigned", "#research"],
    ["私人池缺邮箱", "missing_email", "#research"],
    ["缺客户画像", "missing_profile", "#research"],
    ["缺邮件草稿", "missing_draft", "#outreach"],
    ["草稿待审核", "awaiting_approval", "#outreach"],
    ["审核后待发送", "approved_not_sent", "#outreach"],
    ["打开未回复", "opened_no_reply", "#followup"],
    ["退信", "bounced", "#followup"],
  ];
  return <section className="pipeline-blockers"><div><strong>当前阻塞</strong><span>优先处理数量较高的环节</span></div><nav>{items.map(([label, key, href]) => <a href={href} key={key} className={Number(blockers[key] || 0) ? "has-items" : ""}><b>{Number(blockers[key] || 0)}</b><span>{label}</span></a>)}</nav></section>;
}

function ProviderStats({ rows }) {
  const totals = rows.reduce((acc, row) => {
    const key = row.provider;
    if (!acc[key]) acc[key] = { provider: key, calls: 0, candidates: 0, valid_candidates: 0, selected: 0, errors: 0, credits_used: 0 };
    for (const field of ["calls", "candidates", "valid_candidates", "selected", "errors", "credits_used"]) {
      acc[key][field] += Number(row[field] || 0);
    }
    return acc;
  }, {});
  const summarized = Object.values(totals).sort((a, b) => b.calls - a.calls).slice(0, 8);
  return (
    <section>
      <h3>邮箱 Provider 统计</h3>
      <table className="mini-table">
        <thead><tr><th>Provider</th><th>调用</th><th>候选</th><th>Valid</th><th>选中</th><th>命中率</th><th>错误</th></tr></thead>
        <tbody>
          {summarized.length ? summarized.map((row) => (
            <tr key={row.provider}>
              <td>{row.provider}</td>
              <td>{row.calls}</td>
              <td>{row.candidates}</td>
              <td>{row.valid_candidates}</td>
              <td>{row.selected}</td>
              <td>{row.calls ? `${Math.round((row.selected / row.calls) * 100)}%` : "0%"}</td>
              <td>{row.errors}</td>
            </tr>
          )) : <tr><td colSpan="7">暂无数据</td></tr>}
        </tbody>
      </table>
    </section>
  );
}

function OpsCard({ label, value }) {
  return <article><span>{label}</span><strong>{Number(value || 0)}</strong></article>;
}

function Followups({ contacts, tasks, reload }) {
  const openedNoReply = contacts.filter((c) => Number(c.opened_count || 0) > 0 && !["replied", "bounced", "unsubscribed"].includes(c.status));
  const replied = contacts.filter((c) => c.status === "replied" || Number(c.replied_count || 0) > 0);
  const bounced = contacts.filter((c) => c.status === "bounced" || Number(c.bounced_count || 0) > 0);
  return (
    <section className="followups" id="followups">
      <div className="followup-head">
        <div><span className="eyebrow">Sales follow-up</span><h2>今日待办</h2></div>
        <p>优先处理已打开未回复、已回复和退信客户。</p>
      </div>
      <TodayTaskQueue tasks={tasks} reload={reload} />
      <div className="followup-grid signal-summary">
        <FollowupCard title="已打开未回复" hint="建议今天跟进或准备下一封" tone="hot" contacts={openedNoReply} />
        <FollowupCard title="已回复" hint="需要销售马上接手沟通" tone="reply" contacts={replied} />
        <FollowupCard title="退信需处理" hint="检查邮箱质量或加入黑名单" tone="risk" contacts={bounced} />
      </div>
    </section>
  );
}

function TodayTaskQueue({ tasks, reload }) {
  const [busyId, setBusyId] = useState(null);
  const now = Date.now();
  const rows = [...(tasks || [])].sort((left, right) => {
    const rank = { urgent: 0, high: 1, normal: 2, low: 3 };
    return (rank[left.priority] ?? 4) - (rank[right.priority] ?? 4)
      || new Date(left.due_at || "2999-01-01").getTime() - new Date(right.due_at || "2999-01-01").getTime();
  });

  async function complete(task) {
    setBusyId(task.id);
    try {
      await api("/api/followup-tasks/complete", {
        method: "POST",
        body: JSON.stringify({ task_id: task.id, outcome: "sales_completed" }),
      });
      window.dispatchEvent(new CustomEvent("salesbot:notice", { detail: { message: "待办已完成" } }));
      await reload();
      window.dispatchEvent(new CustomEvent("salesbot:contacts-refresh"));
    } finally {
      setBusyId(null);
    }
  }

  function open(task) {
    window.location.hash = task.task_type === "enrich_contact" ? "research" : "outreach";
    window.setTimeout(() => window.dispatchEvent(new CustomEvent("salesbot:open-contact", { detail: { contactId: task.contact_id } })), 50);
  }

  return (
    <section className="today-task-queue">
      <header>
        <div><strong>系统生成的销售待办</strong><span>系统根据导入、发送和客户反馈自动更新</span></div>
        <b>{rows.length}</b>
      </header>
      <div className="today-task-list">
        {rows.length ? rows.slice(0, 20).map((task) => {
          const due = task.due_at ? new Date(task.due_at) : null;
          const overdue = due && due.getTime() < now;
          return (
            <article key={task.id} className={`sales-task priority-${task.priority || "normal"} ${overdue ? "overdue" : ""}`}>
              <button className="task-main" type="button" onClick={() => open(task)}>
                <span className="task-priority">{taskPriorityLabel(task.priority)}</span>
                <span><strong>{task.title}</strong><small>{task.description || task.company_name || ""}</small></span>
                <em>{formatTaskDue(due, overdue)}</em>
              </button>
              <button className="task-complete" type="button" disabled={busyId === task.id} onClick={() => complete(task)}>{busyId === task.id ? "保存中" : "完成"}</button>
            </article>
          );
        }) : <div className="empty-state compact">当前没有待处理任务</div>}
      </div>
    </section>
  );
}

function taskPriorityLabel(priority) {
  return { urgent: "立即", high: "优先", normal: "常规", low: "稍后" }[priority] || "常规";
}

function formatTaskDue(due, overdue) {
  if (!due || Number.isNaN(due.getTime())) return "无截止时间";
  if (overdue) return "已到期";
  const today = new Date();
  if (due.toDateString() === today.toDateString()) return "今天";
  return `${due.getMonth() + 1}/${due.getDate()}`;
}

function FollowupCard({ title, hint, tone, contacts }) {
  function openContact(contactId) {
    window.location.hash = "outreach";
    window.setTimeout(() => window.dispatchEvent(new CustomEvent("salesbot:open-contact", { detail: { contactId } })), 0);
  }
  return (
    <article className={`followup-card ${tone}`}>
      <div className="followup-title"><div><strong>{title}</strong><span>{hint}</span></div><b>{contacts.length}</b></div>
      <ul>
        {contacts.slice(0, 3).map((contact) => <li key={contact.id}><button type="button" onClick={() => openContact(contact.id)}><div><strong>{fullName(contact)}</strong><span>{contact.company_name || contact.company_domain || ""}</span></div><em>{followupMeta(contact)}</em></button></li>)}
        {!contacts.length && <li className="empty-task">当前没有需要处理的客户</li>}
      </ul>
    </article>
  );
}

function Lifecycle({ lifecycle, contacts }) {
  const stages = lifecycle.stages || {};
  const sabcd = lifecycle.sabcd || {};
  return (
    <section className="lifecycle-board" id="lifecycle-board">
      <div className="followup-head">
        <div><span className="eyebrow">Customer lifecycle</span><h2>客户生命周期漏斗</h2></div>
        <p>用 SABCD 管理客户成熟度，用生命周期管理每一步销售动作。</p>
      </div>
      <div className="sabcd-grid">
        {sabcdStages.map(([key, label, hint]) => {
          const examples = contacts.filter((c) => (c.sabcd_stage || "D") === key).slice(0, 2);
          return <a href="#research" onClick={() => window.dispatchEvent(new CustomEvent("salesbot:contact-filter", { detail: { filter: `sabcd_${key.toLowerCase()}` } }))} key={key} className={`sabcd-card sabcd-${key.toLowerCase()}`}><div><strong>{label}</strong><span>{hint}</span></div><b>{sabcd[key] || 0}</b><small>{examples.length ? examples.map((c) => <em key={c.id}>{fullName(c)}</em>) : <em>暂无客户</em>}</small></a>;
        })}
      </div>
      <div className="lifecycle-grid">
        {lifecycleStages.map(([key, label]) => {
          const examples = contacts.filter((c) => c.lifecycle_stage === key).slice(0, 2);
          return <article key={key} className={`lifecycle-card ${key}`}><strong>{label}</strong><b>{stages[key] || 0}</b><div>{examples.length ? examples.map((c) => <span key={c.id}>{fullName(c)}</span>) : <span>暂无客户</span>}</div></article>;
        })}
      </div>
    </section>
  );
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
    mail_transport: "邮件发送通道",
    database: "数据库连接",
    lead_source: "自动获客 API",
    enrichment: "邮箱富化 API",
    resend: "邮件发送 API",
    sender_email: "发件邮箱域名",
    dry_run: "真实发送开关",
    public_url: "公网访问地址",
    tracking_security: "追踪链接签名",
    reply_ingestion: "回复收件回流",
    admin_password: "管理员密码",
    social_enrichment: "社媒富化 API",
    llm: "AI 文案模型",
    slack: "Slack 通知",
    quotas: "配额配置",
  }[name] || name;
}
