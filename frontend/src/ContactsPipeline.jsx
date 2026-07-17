import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "./api.js";

const statuses = ["new", "enriched", "queued", "sent_1", "sent_2", "sent_3", "replied", "bounced", "unsubscribed"];
const filters = [
  ["unassigned_replies", "待分配回复"],
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
  ["missing_draft", "待生成草稿"],
  ["draft_pending", "草稿待审核"],
  ["draft_approved", "已审核待发送"],
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
  const [pageSize, setPageSize] = useState(() => window.innerWidth <= 720 ? 10 : window.innerWidth <= 1120 ? 15 : 25);
  const [sessionUser, setSessionUser] = useState(() => window.SALESBOT_SESSION?.user || null);
  const [contacts, setContacts] = useState([]);
  const [status, setStatus] = useState("");
  const [filter, setFilter] = useState(() => window.SALESBOT_SESSION?.user?.role === "admin" ? "" : "mine");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [importReport, setImportReport] = useState(null);
  const [page, setPage] = useState(1);
  const [busyContactId, setBusyContactId] = useState(null);
  const [busyAction, setBusyAction] = useState("");
  const [actionFeedback, setActionFeedback] = useState(null);

  useEffect(() => {
    const resize = () => setPageSize(window.innerWidth <= 720 ? 10 : window.innerWidth <= 1120 ? 15 : 25);
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);

  const query = useMemo(() => {
    const params = new URLSearchParams({ limit: "100" });
    if (status) params.set("status", status);
    if (filter) params.set("filter", filter);
    if (search.trim()) params.set("search", search.trim());
    return params.toString();
  }, [status, filter, search]);

  const pageCount = Math.max(1, Math.ceil(contacts.length / pageSize));
  const visibleContacts = useMemo(() => contacts.slice((page - 1) * pageSize, page * pageSize), [contacts, page]);

  useEffect(() => setPage(1), [query]);

  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

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
    const handleFilter = (event) => {
      setStatus("");
      setSearch("");
      setFilter(event.detail?.filter || "");
    };
    window.addEventListener("salesbot:session", handleSession);
    window.addEventListener("salesbot:refresh", handleRefresh);
    window.addEventListener("salesbot:contacts-refresh", handleRefresh);
    window.addEventListener("salesbot:contact-filter", handleFilter);
    return () => {
      window.removeEventListener("salesbot:session", handleSession);
      window.removeEventListener("salesbot:refresh", handleRefresh);
      window.removeEventListener("salesbot:contacts-refresh", handleRefresh);
      window.removeEventListener("salesbot:contact-filter", handleFilter);
    };
  }, [loadContacts]);

  useEffect(() => {
    const timer = window.setTimeout(loadContacts, 250);
    return () => window.clearTimeout(timer);
  }, [loadContacts]);

  async function runContactAction(contact, action) {
    if (busyContactId) return;
    setBusyContactId(contact.id);
    setBusyAction(action);
    setError("");
    if (action === "enrich-email") {
      setActionFeedback({
        tone: "progress",
        title: `正在为 ${fullName(contact)} 查找工作邮箱`,
        message: "正在调用邮箱数据源并验证候选，通常需要 10-30 秒，请勿重复点击。",
        contactId: contact.id,
      });
    } else if (action === "enrich-social") {
      setActionFeedback({
        tone: "progress",
        title: `正在为 ${fullName(contact)} 补齐社媒`,
        message: "正在查询公开社媒资料，请稍候。",
        contactId: contact.id,
      });
    }
    try {
      if (action === "detail") {
        window.location.hash = "outreach";
        window.setTimeout(() => window.dispatchEvent(new CustomEvent("salesbot:open-contact", { detail: { contactId: contact.id } })), 0);
        return;
      }
      if (action === "claim-open") {
        await api("/api/customer-pool/claim", { method: "POST", body: JSON.stringify({ contact_id: contact.id }) });
        window.location.hash = "outreach";
        window.setTimeout(() => window.dispatchEvent(new CustomEvent("salesbot:open-contact", { detail: { contactId: contact.id } })), 50);
        return;
      }
      if (action === "claim") {
        await api("/api/customer-pool/claim", { method: "POST", body: JSON.stringify({ contact_id: contact.id }) });
      } else if (action === "return-public") {
        await api("/api/customer-pool/return", { method: "POST", body: JSON.stringify({ contact_id: contact.id, reason: "manual_return" }) });
      } else if (action === "enrich-email") {
        const result = await api("/api/enrich-one", { method: "POST", body: JSON.stringify({ contact_id: contact.id }) });
        const feedback = emailEnrichmentFeedback(contact, result);
        setActionFeedback(feedback);
        window.dispatchEvent(new CustomEvent("salesbot:notice", { detail: { message: feedback.title, type: feedback.tone === "success" ? "success" : "warning" } }));
      } else if (action === "enrich-social") {
        const result = await api("/api/social-enrich-one", { method: "POST", body: JSON.stringify({ contact_id: contact.id }) });
        const feedback = socialEnrichmentFeedback(contact, result);
        setActionFeedback(feedback);
        window.dispatchEvent(new CustomEvent("salesbot:notice", { detail: { message: feedback.title, type: feedback.tone === "success" ? "success" : "warning" } }));
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
      if (!["enrich-email", "enrich-social"].includes(action)) {
        window.dispatchEvent(new CustomEvent("salesbot:notice", { detail: { message: "客户已更新" } }));
      }
      await loadContacts();
      window.dispatchEvent(new CustomEvent("salesbot:refresh-related"));
    } catch (err) {
      setError(err.message);
      setActionFeedback({ tone: "error", title: "操作失败", message: err.message, contactId: contact.id });
      window.dispatchEvent(new CustomEvent("salesbot:notice", { detail: { message: err.message, type: "error" } }));
    } finally {
      setBusyContactId(null);
      setBusyAction("");
    }
  }

  function openContact(contactId) {
    window.location.hash = "outreach";
    window.setTimeout(() => window.dispatchEvent(new CustomEvent("salesbot:open-contact", { detail: { contactId } })), 0);
  }

  if (!sessionUser) return null;

  return (
    <>
      <div className="section-head">
        <div>
          <span className="eyebrow">Pipeline</span>
          <h2>{filter === "public_pool" ? "公共客户池" : filter === "mine" || filter === "private_pool" ? "我的客户" : "客户列表"}</h2>
          <p>{filter === "public_pool" ? "先查看客户资料和质量，确认适合后领取到自己的客户池。" : "核对身份和邮箱，打开详情生成画像与邮件，再推进触达。"}</p>
        </div>
        <div className="toolbar">
          <label htmlFor="contact-status-filter">Status<select id="contact-status-filter" name="contact_status" value={status} onChange={(event) => setStatus(event.target.value)}><option value="">全部</option>{statuses.map((item) => <option key={item} value={item}>{statusLabel(item)}</option>)}</select></label>
          <label htmlFor="contact-view-filter">视图<select id="contact-view-filter" name="contact_view" value={filter} onChange={(event) => setFilter(event.target.value)}>{filters.map(([value, label]) => <option key={value || "all"} value={value}>{label}</option>)}</select></label>
          <label htmlFor="contact-search">Search<input id="contact-search" name="contact_search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="姓名、公司、邮箱、职位" /></label>
        </div>
      </div>
      <nav className="pipeline-quick-filters" aria-label="客户快捷筛选">
        {[
          ["public_pool", "公共池"],
          ["mine", "我的客户"],
          ["needs_enrichment", "待补资料"],
          ["missing_draft", "待写草稿"],
          ["draft_pending", "待审核"],
          ["draft_approved", "可发送"],
        ].map(([value, label]) => <button key={value} type="button" className={filter === value ? "active" : ""} onClick={() => { setStatus(""); setSearch(""); setFilter(value); }}>{label}</button>)}
      </nav>
      <CustomerStageGuide />
      {actionFeedback && <ActionFeedback feedback={actionFeedback} onNext={() => openContact(actionFeedback.contactId)} onDismiss={() => setActionFeedback(null)} />}
      <ImportBatchReport report={importReport} />
      {error && <div className="admin-alert is-error">{error}</div>}
      <div className="table-shell">
        <table className="customer-table">
          <thead>
            <tr>
              <th>联系人</th><th>公司与联系方式</th><th>身份与质量</th><th>销售阶段</th><th>邮件反馈</th><th>客户池</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            {loading && !contacts.length ? <tr><td colSpan="7"><div className="empty-state">正在加载客户...</div></td></tr> : visibleContacts.length ? visibleContacts.map((contact) => <ContactRow key={contact.id} contact={contact} onAction={runContactAction} busy={busyContactId === contact.id} busyAction={busyContactId === contact.id ? busyAction : ""} />) : <tr><td colSpan="7"><div className="empty-state"><strong>还没有客户</strong><div>先去“获取线索”搜索、导入或手动新增联系人。</div></div></td></tr>}
          </tbody>
        </table>
      </div>
      {contacts.length > pageSize && <div className="table-pagination"><span>共 {contacts.length} 条，每页 {pageSize} 条</span><div><button type="button" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>上一页</button><b>{page} / {pageCount}</b><button type="button" disabled={page >= pageCount} onClick={() => setPage((value) => Math.min(pageCount, value + 1))}>下一页</button></div></div>}
    </>
  );
}

function CustomerStageGuide() {
  return (
    <section className="contact-stage-guide" aria-label="客户处理步骤">
      <article><b>1</b><div><strong>核对身份</strong><span>确认姓名、公司、职位确实匹配</span></div></article>
      <article><b>2</b><div><strong>获得 valid 邮箱</strong><span>候选邮箱不等于可发送邮箱</span></div></article>
      <article><b>3</b><div><strong>生成并审核邮件</strong><span>检查客户证据、主题和正文</span></div></article>
      <article><b>4</b><div><strong>发送并看回流</strong><span>打开、回复后再推进 SABCD</span></div></article>
    </section>
  );
}

function ActionFeedback({ feedback, onNext, onDismiss }) {
  return (
    <section className={`contact-action-feedback ${feedback.tone || "neutral"}`} role="status" aria-live="polite">
      <div><strong>{feedback.title}</strong><span>{feedback.message}</span></div>
      <div className="contact-action-feedback-actions">
        {feedback.tone !== "progress" && feedback.contactId && <button type="button" className="primary soft" onClick={onNext}>{feedback.nextLabel || "查看客户详情"}</button>}
        {feedback.tone !== "progress" && <button type="button" onClick={onDismiss}>关闭</button>}
      </div>
    </section>
  );
}

function ImportBatchReport({ report }) {
  const owners = report?.owners || [];
  if (!owners.length) return null;
  const totals = report?.totals || {};
  const advice = importAdvice(totals);
  return (
    <section className="import-report">
      <div className="import-report-head">
        <div><span className="eyebrow">Latest batch</span><h3>最近导入与触达结果</h3><p>{report.scope === "team" ? "管理员可查看团队批量导入和发送结果。" : "这里只展示你名下客户的处理结果。"}</p></div>
        <div className="import-total-strip"><MetricChip label="导入客户" value={totals.total || 0} /><MetricChip label="有邮箱" value={totals.with_email || 0} /><MetricChip label="已发送" value={totals.sent_total || 0} /><MetricChip label="待发送" value={totals.queued || 0} /></div>
      </div>
      <div className={`import-advice ${advice.tone}`}>
        <strong>{advice.title}</strong>
        <span>{advice.text}</span>
      </div>
      <div className="import-report-grid">
        {owners.map((row) => <article key={row.owner} className="import-owner-card"><div className="import-owner-title"><strong>{row.owner}</strong><span>{row.sent_total ? "已触达" : row.without_email ? "待补邮箱" : "待处理"}</span></div><div className="import-owner-stats"><MetricChip label="导入" value={row.total} /><MetricChip label="邮箱" value={row.with_email} /><MetricChip label="已发" value={row.sent_total} /><MetricChip label="无邮箱" value={row.without_email} /></div><div className="import-owner-foot"><span>new {row.new}</span><span>enriched {row.enriched}</span><span>queued {row.queued}</span><span>sent_1 {row.sent_step_1}</span></div>{row.last_sent_at && <small>最后发送：{formatDate(row.last_sent_at)}</small>}</article>)}
      </div>
    </section>
  );
}

function importAdvice(totals) {
  const total = Number(totals.total || 0);
  const withEmail = Number(totals.with_email || 0);
  const sent = Number(totals.sent_total || 0);
  const withoutEmail = Number(totals.without_email || 0);
  if (!total) return { tone: "neutral", title: "还没有批量结果", text: "导入客户后，这里会显示邮箱命中、待发送和已发送情况。" };
  if (withoutEmail > withEmail) return { tone: "warning", title: "先补邮箱和社媒", text: `本批还有 ${withoutEmail} 个客户没有可发送邮箱，优先跑邮箱/社媒富化，再人工抽查公司邮箱。` };
  if (withEmail > sent) return { tone: "ok", title: "可以准备触达", text: `本批已有 ${withEmail} 个客户带邮箱，其中 ${Math.max(withEmail - sent, 0)} 个还没发送。发送前先看客户画像和邮件正文。` };
  return { tone: "done", title: "本批已完成触达", text: "继续盯邮件中心的打开、回复、退信回流，再按 SABCD 推进客户阶段。" };
}

function MetricChip({ label, value }) {
  return <span className="metric-chip"><b>{Number(value || 0)}</b><em>{label}</em></span>;
}

function ContactRow({ contact, onAction, busy, busyAction }) {
  return (
    <tr>
      <td><div className="customer-identity"><small>#{contact.id}</small><strong>{fullName(contact)}</strong><div className="muted">{contact.job_title || "职位待确认"}</div><div className="inline-links">{isHttpUrl(contact.linkedin_url) && <a className="profile-link" href={contact.linkedin_url} target="_blank" rel="noreferrer">LinkedIn</a>}<SocialProfiles contact={contact} /></div></div></td>
      <td><div className="customer-company"><strong>{contact.company_name || "公司待确认"}</strong><div className="muted">{contact.company_domain || "官网待确认"}</div><div className="contact-line"><span>{displayEmail(contact)}</span><small>{emailMeta(contact)}</small></div><div className="contact-line"><span>{displayPhone(contact)}</span><small>{phoneMeta(contact)}</small></div></div></td>
      <td><IdentityQuality contact={contact} /></td>
      <td><div className="pipeline-cell"><div><span className={`badge ${contact.status || ""}`}>{statusLabel(contact.status)}</span><small>Step {contact.sequence_step || 0}</small></div><span className={`stage-pill sabcd-${String(contact.sabcd_stage || "D").toLowerCase()}`}>{sabcdLabels[contact.sabcd_stage] || "D 未接触"}</span><div className="lifecycle-cell"><span>{lifecycleLabels[contact.lifecycle_stage] || contact.lifecycle_stage || "陌生线索"}</span><small>{dispositionLabel(contact.disposition)}</small></div></div></td>
      <td><EmailFeedback contact={contact} /></td>
      <td><PoolBadge contact={contact} /></td>
      <td><ContactActions contact={contact} onAction={onAction} busy={busy} busyAction={busyAction} /></td>
    </tr>
  );
}

function IdentityQuality({ contact }) {
  const score = Number(contact.identity_confidence ?? contact.lead_score ?? 0);
  const status = contact.identity_status || (score >= 70 ? "likely" : "review");
  return <div className="identity-quality"><div><b>{score || "--"}</b><span>{identityStatusLabel(status)}</span></div><small>{emailQuality(contact)}</small>{contact.enrich_error && <em title={contact.enrich_error}>数据需处理</em>}</div>;
}

function ContactActions({ contact, onAction, busy, busyAction }) {
  const { primary, secondary } = rowActions(contact);
  return (
    <div className="row-actions compact-actions">
      <button type="button" disabled={busy} className="primary soft main-row-action" onClick={() => onAction(contact, primary[0])}>{busy ? busyActionLabel(busyAction) : primary[1]}</button>
      {!!secondary.length && (
        <details className="row-action-menu">
          <summary aria-label="更多客户操作" title="更多客户操作">更多</summary>
          <div>{secondary.map(([action, label]) => <button key={action} disabled={busy} type="button" onClick={() => onAction(contact, action)}>{label}</button>)}</div>
        </details>
      )}
    </div>
  );
}

function rowActions(contact) {
  if (contact.pool_type === "public") {
    return { primary: ["claim-open", "领取并处理"], secondary: [] };
  }
  const blocked = ["bounced", "unsubscribed"].includes(contact.status)
    || Number(contact.bounced_count || 0) > 0
    || Number(contact.unsubscribed_count || 0) > 0
    || Number(contact.complained_count || 0) > 0;
  const hasValidEmail = Boolean(contact.email) && contact.email_status === "valid";
  const candidates = Array.isArray(contact.email_candidates) ? contact.email_candidates : [];
  const hasValidCandidate = candidates.some((item) => item.category === "personal_work" && item.status === "valid");
  const identityNeedsReview = contact.identity_status === "mismatch" || (contact.identity_status === "review" && Number(contact.identity_confidence ?? contact.lead_score ?? 0) < 70);
  const primary = identityNeedsReview
    ? ["detail", "先核对身份"]
    : !hasValidEmail
      ? hasValidCandidate
        ? ["detail", "采用已验证邮箱"]
        : candidates.length
          ? ["detail", "核验邮箱候选"]
          : ["enrich-email", "查找工作邮箱"]
    : blocked || contact.status === "replied" || Number(contact.replied_count || 0) > 0
      ? ["detail", "查看并跟进"]
      : contact.draft_status === "draft"
        ? ["detail", "审核邮件"]
        : contact.draft_status === "approved"
          ? ["detail", "发送邮件"]
          : ["detail", "准备邮件"];
  const queueAction = blocked || contact.status !== "enriched" ? [] : [["queue-one", "加入发送队列"]];
  const retryEmail = !hasValidEmail && candidates.length ? [["enrich-email", "重新查找邮箱"]] : [];
  const secondary = [...retryEmail, ["enrich-social", "补社媒"], ["profile", "生成画像"], ...queueAction, ["next", "推进销售阶段"], ["wait", "进入等待"], ["return-public", "退回公共池"]]
    .filter(([action]) => action !== primary[0]);
  return { primary, secondary };
}

function busyActionLabel(action) {
  if (action === "enrich-email") return "正在查找邮箱...";
  if (action === "enrich-social") return "正在补齐社媒...";
  if (action === "profile") return "正在生成画像...";
  return "正在处理...";
}

function emailEnrichmentFeedback(contact, result) {
  const fields = result?.fields || {};
  if (fields.email && fields.email_status === "valid") {
    return {
      tone: "success",
      title: "已找到并验证工作邮箱",
      message: `${fields.email} 已成为正式收件邮箱。下一步请生成客户画像和邮件草稿。`,
      contactId: contact.id,
      nextLabel: "下一步：准备邮件",
    };
  }
  const candidates = Array.isArray(fields.email_candidates) ? fields.email_candidates : [];
  if (candidates.length) {
    const best = [...candidates].sort((a, b) => Number(b.confidence || 0) - Number(a.confidence || 0))[0];
    return {
      tone: "warning",
      title: "未找到已验证邮箱",
      message: `已保存 ${candidates.length} 个候选，最高 ${Number(best?.confidence || 0)}%（${best?.status || "unknown"}）。候选仍需验证，当前不会进入发送阶段。`,
      contactId: contact.id,
      nextLabel: "查看邮箱候选",
    };
  }
  return {
    tone: "warning",
    title: "本次没有找到可用邮箱",
    message: "请先检查姓名、公司和官网是否属于同一个人；资料正确时可稍后重新查找。",
    contactId: contact.id,
    nextLabel: "检查客户资料",
  };
}

function socialEnrichmentFeedback(contact, result) {
  if (result?.ok) {
    return { tone: "success", title: "社媒资料已补齐", message: `已通过 ${result.provider || "社媒数据源"} 更新公开主页。`, contactId: contact.id };
  }
  return {
    tone: "warning",
    title: "本次没有补到社媒资料",
    message: result?.error || "数据源没有返回公开主页，或当前数据源额度不足。",
    contactId: contact.id,
  };
}

function PoolBadge({ contact }) {
  const isPublic = contact.pool_type === "public";
  return <div className="pool-cell"><span className={`pool-pill ${isPublic ? "public" : "private"}`}>{isPublic ? "公共池" : "私人池"}</span><div className="muted">{isPublic ? "可领取" : (contact.owner || "已分配")}</div>{!isPublic && contact.pool_expires_at && <div className="muted">保护期至 {formatDate(contact.pool_expires_at)}</div>}{isPublic && contact.returned_to_public_at && <div className="muted">回池 {formatDate(contact.returned_to_public_at)}</div>}{Number(contact.claim_count || 0) > 0 && <div className="muted">领取 {contact.claim_count} 次</div>}</div>;
}

function SocialProfiles({ contact }) {
  const profiles = contact.social_profiles || {};
  const entries = [["linkedin", "LinkedIn"], ["twitter", "X"], ["github", "GitHub"], ["facebook", "Facebook"], ["website", "Website"]].filter(([key]) => isHttpUrl(profiles[key]));
  if (entries.length) return <div className="social-links">{entries.map(([key, label]) => <a key={key} className="social-link" href={profiles[key]} target="_blank" rel="noreferrer">{label}</a>)}</div>;
  if (contact.social_error) return <span className="muted" title={contact.social_error}>社媒未找到</span>;
  return <span className="muted">社媒待补</span>;
}

function EmailFeedback({ contact }) {
  const items = [];
  if (contact.draft_status === "draft") items.push(["draft", "草稿待审核"]);
  if (contact.draft_status === "approved") items.push(["approved", "已审核待发送"]);
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
  const parts = [emailQuality(contact)];
  if (contact.email_status) parts.push(contact.email_status);
  if (contact.email_source) parts.push(contact.email_source);
  if (contact.email_confidence !== null && contact.email_confidence !== undefined) parts.push(`${contact.email_confidence}%`);
  return parts.filter(Boolean).join(" · ");
}

function emailQuality(contact) {
  const email = String(contact.email || "");
  if (!email || email.includes("*")) return "待验证";
  const local = email.split("@", 1)[0].toLowerCase();
  const roleBased = new Set(["admin", "billing", "contact", "hello", "help", "info", "office", "press", "sales", "support", "team"]);
  if (roleBased.has(local)) return "公司邮箱";
  if (contact.email_status !== "valid") return "待验证";
  const leadScore = Number(contact.lead_score ?? 60);
  const title = String(contact.job_title || "").toLowerCase();
  if (Number(contact.bounced_count || 0) > 0 || contact.status === "bounced") return "退信风险";
  if (leadScore < 50 || /(assistant|customer service|intern|reception|receptionist|support)/.test(title)) return "低优先级";
  if (Number(contact.email_confidence ?? 100) < 70) return "待确认";
  return "可发送";
}

function displayPhone(contact) {
  if (contact.phone) return contact.phone;
  const candidates = Array.isArray(contact.phone_candidates) ? contact.phone_candidates : [];
  if (candidates[0]?.phone) return candidates[0].phone;
  return "待补充";
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

function identityStatusLabel(status) {
  return { confirmed: "身份已确认", likely: "较可能", review: "需复核", mismatch: "不匹配" }[status] || "需复核";
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
