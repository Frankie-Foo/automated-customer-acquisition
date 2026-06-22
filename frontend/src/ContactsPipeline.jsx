import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "./api.js";

const statuses = ["new", "enriched", "queued", "sent_1", "sent_2", "sent_3", "replied", "bounced", "unsubscribed"];
const filters = [
  ["", "全部客户"],
  ["mine", "我的客户"],
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
      const data = await api(`/api/contacts?${query}`);
      const rows = data.contacts || [];
      setContacts(rows);
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
      if (action === "enrich-email") {
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
          <p>销售团队每天在这里查看客户状态、最近触达和邮件行为反馈。</p>
        </div>
        <div className="toolbar">
          <label>
            Status
            <select value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="">全部</option>
              {statuses.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
          <label>
            视图
            <select value={filter} onChange={(event) => setFilter(event.target.value)}>
              {filters.map(([value, label]) => <option key={value || "all"} value={value}>{label}</option>)}
            </select>
          </label>
          <label>
            Search
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="姓名、公司、邮箱、职位" />
          </label>
        </div>
      </div>
      {error && <div className="admin-alert is-error">{error}</div>}
      <div className="table-shell">
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>联系人</th>
              <th>公司</th>
              <th>邮箱</th>
              <th>电话</th>
              <th>状态</th>
              <th>Step</th>
              <th>社媒</th>
              <th>邮件反馈</th>
              <th>生命周期</th>
              <th>客户画像</th>
              <th>最近联系</th>
              <th>操作</th>
              <th>错误</th>
            </tr>
          </thead>
          <tbody>
            {loading && !contacts.length ? (
              <tr><td colSpan="14"><div className="empty-state">正在加载客户...</div></td></tr>
            ) : contacts.length ? (
              contacts.map((contact) => <ContactRow key={contact.id} contact={contact} onAction={runContactAction} />)
            ) : (
              <tr>
                <td colSpan="14">
                  <div className="empty-state">
                    <strong>还没有客户</strong>
                    <div>先用上方“自动获客”、CSV 导入，或手动新增一个联系人。</div>
                  </div>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}

function ContactRow({ contact, onAction }) {
  const insights = contact.profile_insights || {};
  const score = Number(insights.icp_fit_score ?? 0);
  return (
    <tr>
      <td>{contact.id}</td>
      <td>
        <strong>{fullName(contact)}</strong>
        <div className="muted">{contact.job_title || ""}</div>
        {isHttpUrl(contact.linkedin_url) && <a className="profile-link" href={contact.linkedin_url} target="_blank" rel="noreferrer">LinkedIn</a>}
      </td>
      <td><strong>{contact.company_name || ""}</strong><div className="muted">{contact.company_domain || ""}</div></td>
      <td>{displayEmail(contact)}<div className="muted">{emailMeta(contact)}</div></td>
      <td>{displayPhone(contact)}<div className="muted">{phoneMeta(contact)}</div></td>
      <td><span className={`badge ${contact.status || ""}`}>{statusLabel(contact.status)}</span></td>
      <td>{contact.sequence_step || 0}</td>
      <td><SocialProfiles contact={contact} /></td>
      <td><EmailFeedback contact={contact} /></td>
      <td>
        <div className="lifecycle-cell">
          <span className="stage-pill">{lifecycleLabels[contact.lifecycle_stage] || contact.lifecycle_stage || "陌生线索"}</span>
          <div className="muted">{dispositionLabel(contact.disposition)}</div>
          {contact.next_action_at && <div className="muted">下次：{formatDate(contact.next_action_at)}</div>}
        </div>
      </td>
      <td>
        {contact.profile_summary || Object.keys(insights).length ? (
          <div className="profile-insights" title={contact.profile_summary || ""}>
            <div className="fit-line"><b>{score || "--"}</b><span>{intentLabel(insights.intent_level)}</span></div>
            <strong>{insights.persona || contact.profile_summary || "客户画像"}</strong>
            <p>{insights.next_action || contact.profile_summary || "暂无下一步建议"}</p>
          </div>
        ) : <div className="profile-summary muted">待生成画像</div>}
      </td>
      <td>{formatDate(contact.last_contacted_at)}</td>
      <td>
        <div className="row-actions">
          {[
            ["enrich-email", "邮箱"],
            ["enrich-social", "社媒"],
            ["queue-one", "入队"],
            ["send-one", "发送"],
            ["next", "推进"],
            ["wait", "等待"],
            ["abandon", "放弃"],
            ["profile", "画像"],
            ["detail", "详情"],
          ].map(([action, label]) => <button key={action} type="button" onClick={() => onAction(contact, action)}>{label}</button>)}
        </div>
      </td>
      <td className="error-text" title={contact.enrich_error || ""}>{contact.enrich_error || ""}</td>
    </tr>
  );
}

function SocialProfiles({ contact }) {
  const profiles = contact.social_profiles || {};
  const entries = [
    ["linkedin", "LinkedIn"],
    ["twitter", "X"],
    ["github", "GitHub"],
    ["facebook", "Facebook"],
    ["website", "Website"],
  ].filter(([key]) => isHttpUrl(profiles[key]));
  if (entries.length) {
    return <div className="social-links">{entries.map(([key, label]) => <a key={key} className="social-link" href={profiles[key]} target="_blank" rel="noreferrer">{label}</a>)}</div>;
  }
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
  return parts.join(" · ");
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
  return {
    new: "新线索",
    enriched: "已富化",
    queued: "待发送",
    sent_1: "已发第 1 封",
    sent_2: "已发第 2 封",
    sent_3: "已发第 3 封",
    replied: "已回复",
    bounced: "已退信",
    unsubscribed: "已退订",
  }[status] || status;
}

function dispositionLabel(disposition) {
  return {
    active: "推进中",
    waiting: "等待",
    abandoned: "已放弃",
    won: "已签约",
    lost: "流失",
  }[disposition] || disposition || "推进中";
}

function intentLabel(level) {
  return { high: "高意向", medium: "中意向", low: "低意向", unknown: "待判断" }[level] || "待判断";
}

function formatDate(value) {
  if (!value) return "";
  return String(value).replace("T", " ").slice(0, 16);
}

function lifecyclePayload(action, contact) {
  if (action === "next") {
    const order = ["lead", "replied", "conversation", "meeting", "business_plan", "trial_order", "agency_agreement", "store_creation", "signed"];
    const current = contact.lifecycle_stage || "lead";
    const next = order[Math.min(order.indexOf(current) + 1 || 1, order.length - 1)];
    return { contact_id: contact.id, lifecycle_stage: next, disposition: "active", notes: "react pipeline: advance lifecycle" };
  }
  if (action === "wait") {
    return { contact_id: contact.id, lifecycle_stage: contact.lifecycle_stage || "waiting_pool", disposition: "waiting", next_action_at: new Date(Date.now() + 7 * 86400000).toISOString(), notes: "react pipeline: move to waiting pool" };
  }
  return { contact_id: contact.id, lifecycle_stage: "abandoned", disposition: "abandoned", lost_reason: "react pipeline: manually abandoned", notes: "react pipeline: abandon customer" };
}
