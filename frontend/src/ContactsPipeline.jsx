import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "./api.js";

const statuses = ["new", "enriched", "queued", "sent_1", "sent_2", "sent_3", "replied", "bounced", "unsubscribed"];
const filters = [
  ["", "全部客户"],
  ["public_pool", "公共客户池"],
  ["private_pool", "私人客户池"],
  ["pool_expiring", "14天内到期"],
  ["returned_pool", "已回公共池"],
  ["mine", "我的客户"],
  ["sabcd_d", "D 未接触"],
  ["sabcd_c", "C 已触达"],
  ["sabcd_b", "B 多轮沟通"],
  ["sabcd_a", "A 商业计划/试订单"],
  ["sabcd_s", "S 签约建店"],
  ["needs_enrichment", "待富化"],
  ["ready_to_send", "有邮箱待发送"],
  ["opened_no_reply", "已打开未回复"],
  ["replied", "已回复"],
  ["bounced", "退信需处理"],
  ["second_touch_due", "第2次触达待发送"],
  ["third_touch_due", "第3次触达待发送"],
  ["waiting_pool", "等待池"],
  ["abandoned", "已放弃"],
];

const lifecycleLabels = {
  lead: "陌生线索",
  replied: "已回复",
  conversation: "初步沟通",
  meeting: "约会/会议",
  business_plan: "商业计划",
  store_visit: "到店参观",
  trial_order: "试订单",
  agency_agreement: "代理协议",
  hq_visit: "总部拜访",
  signed: "成功签约",
  maintenance: "持续维护",
  waiting_pool: "等待池",
  abandoned: "已放弃",
};

const sabcdLabels = {
  D: "D 未接触",
  C: "C 已触达",
  B: "B 多轮沟通",
  A: "A 商业计划/试订单",
  S: "S 签约建店",
};

export default function ContactsPipelinePortal() {
  const [target, setTarget] = useState(null);

  useEffect(() => {
    const node = document.querySelector("#react-pipeline-root");
    const pipeline = document.querySelector("#pipeline");
    pipeline?.classList.add("react-contacts-enabled");
    setTarget(node);
    return () => pipeline?.classList.remove("react-contacts-enabled");
  }, []);

  if (!target) return null;
  return createPortal(<ContactsPipeline />, target);
}

function ContactsPipeline() {
  const [sessionUser, setSessionUser] = useState(null);
  const [contacts, setContacts] = useState([]);
  const [status, setStatus] = useState("");
  const [filter, setFilter] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [importReport, setImportReport] = useState(null);

  const query = useMemo(() => {
    const params = new URLSearchParams({ limit: "100" });
    if (status) params.set("status", status);
    if (filter) params.set("filter", filter);
    if (search.trim()) params.set("search", search.trim());
    return params.toString();
  }, [status, filter, search]);

  const loadContacts = useCallback(async () => {
    if (!sessionUser) return;
    setLoading(true);
    setError("");
    try {
      const [data, report] = await Promise.all([
        api(`/api/contacts?${query}`),
        api("/api/owner-import-report"),
      ]);
      const rows = data.contacts || [];
      setContacts(rows);
      setImportReport(report);
      window.latestContacts = rows;
      window.dispatchEvent(new CustomEvent("salesbot:contacts-updated", { detail: { contacts: rows } }));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [query, sessionUser]);

  useEffect(() => {
    const handleSession = (event) => setSessionUser(event.detail?.user || null);
    const handleRefresh = () => loadContacts();
    window.addEventListener("salesbot:session", handleSession);
    window.addEventListener("salesbot:refresh", handleRefresh);
    window.addEventListener("salesbot:contacts-refresh", handleRefresh);
    return () => {
      window.removeEventListener("salesbot:session", handleSession);
      window.removeEventListener("salesbot:refresh", handleRefresh);
      window.removeEventListener("salesbot:contacts-refresh", handleRefresh);
    };
  }, [loadContacts]);

  useEffect(() => {
    api("/api/me").then((session) => setSessionUser(session.user)).catch(() => setSessionUser(null));
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(loadContacts, 250);
    return () => window.clearTimeout(timer);
  }, [loadContacts]);

  async function runContactAction(contact, action) {
    setError("");
    try {
      if (action === "detail") {
        window.dispatchEvent(new CustomEvent("salesbot:open-contact", { detail: { contactId: contact.id } }));
        return;
      }
      if (action === "claim") {
        await api("/api/customer-pool/claim", { method: "POST", body: JSON.stringify({ contact_id: contact.id }) });
      } else if (action === "return-public") {
        await api("/api/customer-pool/return", { method: "POST", body: JSON.stringify({ contact_id: contact.id, reason: "manual_return" }) });
      } else if (action === "enrich-email") {
        await api("/api/enrich-one", { method: "POST", body: JSON.stringify({ contact_id: contact.id }) });
      } else if (action === "enrich-social") {
        await api("/api/social-enrich-one", { method: "POST", body: JSON.stringify({ contact_id: contact.id }) });
      } else if (action === "queue-one") {
        await api("/api/queue-one", { method: "POST", body: JSON.stringify({ contact_id: contact.id }) });
      } else if (action === "send-one") {
        const result = await api("/api/send-one", { method: "POST", body: JSON.stringify({ contact_id: contact.id }) });
        window.dispatchEvent(new CustomEvent("salesbot:usage", { detail: { usage: result.usage } }));
      } else if (action === "profile") {
        await api("/api/profile-agent", { method: "POST", body: JSON.stringify({ contact_id: contact.id }) });
      } else {
        await api("/api/lifecycle", { method: "POST", body: JSON.stringify(lifecyclePayload(action, contact)) });
      }
      window.dispatchEvent(new CustomEvent("salesbot:notice", { detail: { message: "客户已更新" } }));
      await loadContacts();
      window.dispatchEvent(new CustomEvent("salesbot:refresh-related"));
    } catch (err) {
      setError(err.message);
      window.dispatchEvent(new CustomEvent("salesbot:notice", { detail: { message: err.message, type: "error" } }));
    }
  }

  if (!sessionUser) return null;

  return (
    <>
      <div className="section-head">
        <div>
          <span className="eyebrow">Pipeline</span>
          <h2>客户列表与邮件反馈</h2>
          <p>查看客户状态、最近触达、邮件行为和 SABCD 阶段。</p>
        </div>
        <div className="toolbar">
          <label>Status<select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">全部</option>{statuses.map((item) => <option key={item} value={item}>{statusLabel(item)}</option>)}</select></label>
          <label>视图<select value={filter} onChange={(event) => setFilter(event.target.value)}>{filters.map(([value, label]) => <option key={value || "all"} value={value}>{label}</option>)}</select></label>
          <label>Search<input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="姓名、公司、邮箱、职位" /></label>
        </div>
      </div>
      <ImportBatchReport report={importReport} />
      {error && <div className="admin-alert is-error">{error}</div>}
      <div className="table-shell">
        <table>
          <thead>
            <tr>
              <th>ID</th><th>联系人</th><th>公司</th><th>邮箱</th><th>电话</th><th>状态</th><th>Step</th><th>社媒</th><th>邮件反馈</th><th>SABCD</th><th>客户池</th><th>生命周期</th><th>客户画像</th><th>最近联系</th><th>操作</th><th>错误</th>
            </tr>
          </thead>
          <tbody>
            {loading && !contacts.length ? <tr><td colSpan="16"><div className="empty-state">正在加载客户...</div></td></tr> : contacts.length ? contacts.map((contact) => <ContactRow key={contact.id} contact={contact} onAction={runContactAction} />) : <tr><td colSpan="16"><div className="empty-state"><strong>还没有客户</strong><div>先用上方获客、CSV 导入，或手动新增联系人。</div></div></td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}

function ImportBatchReport({ report }) {
  const owners = report?.owners || [];
  if (!owners.length) return null;
  const totals = report?.totals || {};
  return (
    <section className="import-report">
      <div className="import-report-head">
        <div><span className="eyebrow">Latest batch</span><h3>最近导入与触达结果</h3><p>{report.scope === "team" ? "管理员可查看团队批量导入和发送结果。" : "这里只展示你名下客户的处理结果。"}</p></div>
        <div className="import-total-strip"><MetricChip label="导入客户" value={totals.total || 0} /><MetricChip label="有邮箱" value={totals.with_email || 0} /><MetricChip label="已发送" value={totals.sent_total || 0} /><MetricChip label="待发送" value={totals.queued || 0} /></div>
      </div>
      <div className="import-report-grid">
        {owners.map((row) => <article key={row.owner} className="import-owner-card"><div className="import-owner-title"><strong>{row.owner}</strong><span>{row.sent_total ? "已触达" : row.without_email ? "待补邮箱" : "待处理"}</span></div><div className="import-owner-stats"><MetricChip label="导入" value={row.total} /><MetricChip label="邮箱" value={row.with_email} /><MetricChip label="已发" value={row.sent_total} /><MetricChip label="无邮箱" value={row.without_email} /></div><div className="import-owner-foot"><span>new {row.new}</span><span>enriched {row.enriched}</span><span>queued {row.queued}</span><span>sent_1 {row.sent_step_1}</span></div>{row.last_sent_at && <small>最后发送：{formatDate(row.last_sent_at)}</small>}</article>)}
      </div>
    </section>
  );
}

function MetricChip({ label, value }) {
  return <span className="metric-chip"><b>{Number(value || 0)}</b><em>{label}</em></span>;
}

function ContactRow({ contact, onAction }) {
  const insights = contact.profile_insights || {};
  const score = Number(insights.icp_fit_score ?? 0);
  return (
    <tr>
      <td>{contact.id}</td>
      <td><strong>{fullName(contact)}</strong><div className="muted">{contact.job_title || ""}</div>{isHttpUrl(contact.linkedin_url) && <a className="profile-link" href={contact.linkedin_url} target="_blank" rel="noreferrer">LinkedIn</a>}</td>
      <td><strong>{contact.company_name || ""}</strong><div className="muted">{contact.company_domain || ""}</div></td>
      <td>{displayEmail(contact)}<div className="muted">{emailMeta(contact)}</div></td>
      <td>{displayPhone(contact)}<div className="muted">{phoneMeta(contact)}</div></td>
      <td><span className={`badge ${contact.status || ""}`}>{statusLabel(contact.status)}</span></td>
      <td>{contact.sequence_step || 0}</td>
      <td><SocialProfiles contact={contact} /></td>
      <td><EmailFeedback contact={contact} /></td>
      <td><span className={`stage-pill sabcd-${String(contact.sabcd_stage || "D").toLowerCase()}`}>{sabcdLabels[contact.sabcd_stage] || "D 未接触"}</span></td>
      <td><PoolBadge contact={contact} /></td>
      <td><div className="lifecycle-cell"><span className="stage-pill">{lifecycleLabels[contact.lifecycle_stage] || contact.lifecycle_stage || "陌生线索"}</span><div className="muted">{dispositionLabel(contact.disposition)}</div>{contact.next_action_at && <div className="muted">下次：{formatDate(contact.next_action_at)}</div>}</div></td>
      <td>{contact.profile_summary || Object.keys(insights).length ? <div className="profile-insights" title={contact.profile_summary || ""}><div className="fit-line"><b>{score || "--"}</b><span>{intentLabel(insights.intent_level)}</span></div><strong>{insights.persona || contact.profile_summary || "客户画像"}</strong><p>{insights.next_action || contact.profile_summary || "暂无下一步建议"}</p></div> : <div className="profile-summary muted">待生成画像</div>}</td>
      <td>{formatDate(contact.last_contacted_at)}</td>
      <td><div className="row-actions">{rowActions(contact).map(([action, label]) => <button key={action} type="button" onClick={() => onAction(contact, action)}>{label}</button>)}</div></td>
      <td className="error-text" title={contact.enrich_error || ""}>{contact.enrich_error || ""}</td>
    </tr>
  );
}

function rowActions(contact) {
  if (contact.pool_type === "public") return [["claim", "领取"], ["profile", "画像"], ["detail", "详情"]];
  return [["enrich-email", "邮箱"], ["enrich-social", "社媒"], ["queue-one", "入队"], ["send-one", "发送"], ["next", "推进"], ["stage-d", "D"], ["stage-c", "C"], ["stage-b", "B"], ["stage-a", "A"], ["stage-s", "S"], ["wait", "等待"], ["abandon", "放弃"], ["profile", "画像"], ["detail", "详情"], ["return-public", "退回公池"]];
}

function PoolBadge({ contact }) {
  const isPublic = contact.pool_type === "public";
  return <div className="pool-cell"><span className={`pool-pill ${isPublic ? "public" : "private"}`}>{isPublic ? "公共池" : "私人池"}</span><div className="muted">{isPublic ? "可领取" : (contact.owner || "已分配")}</div>{!isPublic && contact.pool_expires_at && <div className="muted">保护期至 {formatDate(contact.pool_expires_at)}</div>}{isPublic && contact.returned_to_public_at && <div className="muted">回池 {formatDate(contact.returned_to_public_at)}</div>}{Number(contact.claim_count || 0) > 0 && <div className="muted">领取 {contact.claim_count} 次</div>}</div>;
}

function SocialProfiles({ contact }) {
  const profiles = contact.social_profiles || {};
  const entries = [["linkedin", "LinkedIn"], ["twitter", "X"], ["github", "GitHub"], ["facebook", "Facebook"], ["website", "Website"]].filter(([key]) => isHttpUrl(profiles[key]));
  if (entries.length) return <div className="social-links">{entries.map(([key, label]) => <a key={key} className="social-link" href={profiles[key]} target="_blank" rel="noreferrer">{label}</a>)}</div>;
  if (contact.social_error) return <span className="muted" title={contact.social_error}>未找到</span>;
  return <span className="muted">待富化</span>;
}

function EmailFeedback({ contact }) {
  const items = [];
  if (Number(contact.delivered_count || 0) > 0) items.push(["delivered", `已送达 ${contact.delivered_count}`]);
  if (Number(contact.sent_count || 0) > 0) items.push(["sent", `已发送 ${contact.sent_count}`]);
  if (Number(contact.opened_count || 0) > 0) items.push(["opened", `已打开 ${contact.opened_count}`]);
  if (Number(contact.clicked_count || 0) > 0) items.push(["clicked", `已点击 ${contact.clicked_count}`]);
  if (Number(contact.replied_count || 0) > 0) items.push(["replied", "已回复"]);
  if (Number(contact.bounced_count || 0) > 0) items.push(["bounced", "已退信"]);
  if (Number(contact.unsubscribed_count || 0) > 0) items.push(["unsubscribed", "已退订"]);
  if (!items.length) return <span className="muted">暂无反馈</span>;
  return <div className="event-list">{items.map(([type, label]) => <span key={type} className={`event-chip ${type}`}>{label}</span>)}</div>;
}

function fullName(contact) {
  return [contact.first_name, contact.last_name].filter(Boolean).join(" ") || "(No name)";
}

function isHttpUrl(value) {
  return /^https?:\/\//i.test(String(value || ""));
}

function displayEmail(contact) {
  if (!contact.email || String(contact.email).includes("*")) return "待富化";
  return contact.email;
}

function emailMeta(contact) {
  const parts = [contact.email_status || "unknown"];
  if (contact.email_source) parts.push(contact.email_source);
  if (contact.email_confidence !== null && contact.email_confidence !== undefined) parts.push(`${contact.email_confidence}%`);
  const quality = emailQuality(contact);
  if (quality) parts.push(quality);
  return parts.join(" · ");
}

function emailQuality(contact) {
  const email = String(contact.email || "");
  if (!email || email.includes("*")) return "not ready";
  const local = email.split("@", 1)[0].toLowerCase();
  const roleBased = new Set(["admin", "billing", "contact", "hello", "help", "info", "office", "press", "sales", "support", "team"]);
  if (contact.email_status !== "valid" || roleBased.has(local)) return "blocked";
  const leadScore = Number(contact.lead_score ?? 60);
  const title = String(contact.job_title || "").toLowerCase();
  if (leadScore < 50 || /(assistant|customer service|intern|reception|receptionist|support)/.test(title)) return "blocked";
  if (Number(contact.email_confidence ?? 100) < 70) return "review";
  return "sendable";
}

function displayPhone(contact) {
  if (contact.phone) return contact.phone;
  const candidates = Array.isArray(contact.phone_candidates) ? contact.phone_candidates : [];
  return candidates[0]?.phone || "待补充";
}

function phoneMeta(contact) {
  const candidates = Array.isArray(contact.phone_candidates) ? contact.phone_candidates : [];
  if (contact.phone) return "provided";
  if (candidates.length) return `${candidates.length} 个候选`;
  return "";
}

function statusLabel(status) {
  return { new: "新线索", enriched: "已富化", queued: "待发送", sent_1: "已发第1封", sent_2: "已发第2封", sent_3: "已发第3封", replied: "已回复", bounced: "已退信", unsubscribed: "已退订" }[status] || status;
}

function dispositionLabel(disposition) {
  return { active: "推进中", waiting: "等待", abandoned: "已放弃", won: "已签约", lost: "流失" }[disposition] || disposition || "推进中";
}

function intentLabel(level) {
  return { high: "高意向", medium: "中意向", low: "低意向", unknown: "待判断" }[level] || "待判断";
}

function formatDate(value) {
  if (!value) return "";
  return String(value).replace("T", " ").slice(0, 16);
}

function lifecyclePayload(action, contact) {
  if (action.startsWith("stage-")) {
    const stage = action.slice(-1).toUpperCase();
    return { contact_id: contact.id, sabcd_stage: stage, notes: `set SABCD stage ${stage}` };
  }
  if (action === "next") {
    const order = ["lead", "replied", "conversation", "meeting", "business_plan", "trial_order", "agency_agreement", "store_visit", "signed"];
    const current = contact.lifecycle_stage || "lead";
    const currentIndex = Math.max(0, order.indexOf(current));
    const next = order[Math.min(currentIndex + 1, order.length - 1)];
    return { contact_id: contact.id, lifecycle_stage: next, disposition: "active", notes: "advance lifecycle" };
  }
  if (action === "wait") return { contact_id: contact.id, lifecycle_stage: contact.lifecycle_stage || "waiting_pool", disposition: "waiting", next_action_at: new Date(Date.now() + 7 * 86400000).toISOString(), notes: "move to waiting pool" };
  return { contact_id: contact.id, lifecycle_stage: "abandoned", disposition: "abandoned", lost_reason: "manually abandoned", notes: "abandon customer" };
}
