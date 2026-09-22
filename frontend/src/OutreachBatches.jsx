import { useEffect, useId, useState } from "react";
import { api } from "./api.js";
import { openContactWorkspace } from "./workspaceNavigation.js";
import "./OutreachBatches.css";

const pageSize = 25;
const summaryFields = [
  ["total_count", "客户总数"],
  ["profiled_contacts", "有背调记录"],
  ["drafted_contacts", "有草稿客户"],
  ["sent_contacts", "已发送客户"],
  ["sent_messages", "已发送邮件"],
  ["replied_contacts", "已回复客户"],
  ["failed_messages", "失败邮件"],
  ["blocked_contacts", "受阻客户"],
];
const lifecycleLabels = {
  lead: "陌生线索", replied: "已回复", conversation: "初步沟通", meeting: "约会/会议",
  business_plan: "商业计划", store_visit: "到店参观", trial_order: "试订单",
  agency_agreement: "代理协议", hq_visit: "总部拜访", store_creation: "门店创建",
  signed: "成功签约", maintenance: "持续维护", waiting_pool: "等待池", abandoned: "已放弃",
};
const statusLabels = {
  new: "新线索", enriched: "已富化", queued: "已入队", pending: "待处理",
  draft: "草稿", approved: "已审核", sending: "发送中", sent: "已发送",
  sent_1: "已发送第1封", sent_2: "已发送第2封", sent_3: "已发送第3封",
  delivered: "已送达", opened: "已打开", replied: "已回复", auto_reply: "自动回复",
  failed: "发送失败", blocked: "受阻", bounced: "退信", unsubscribed: "已退订",
  complained: "投诉", skipped: "已跳过", dry_run: "发送演练",
};
const eligibilityLabels = {
  ownership_changed: "客户归属已变更", email_unverified: "邮箱尚未验证",
  unsubscribed: "客户已退订", bounced: "邮箱退信", complained: "客户投诉", blocked: "客户已被阻止发送",
};

export default function OutreachBatches() {
  const headingId = useId();
  const [batches, setBatches] = useState(null);
  const [selectedId, setSelectedId] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    api("/api/outreach-batches", { signal: controller.signal }).then((data) => {
      if (controller.signal.aborted) return;
      if (!Array.isArray(data.batches)) throw new Error("批次数据格式错误");
      setBatches(data.batches);
      setSelectedId((id) => data.batches.some((batch) => String(batch.id) === id)
        ? id : String(data.batches[0]?.id ?? ""));
    }).catch((err) => {
      if (!controller.signal.aborted) setError(err.message);
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [revision]);

  useEffect(() => {
    const refresh = () => setRevision((value) => value + 1);
    window.addEventListener("salesbot:contacts-refresh", refresh);
    window.addEventListener("salesbot:refresh-related", refresh);
    return () => {
      window.removeEventListener("salesbot:contacts-refresh", refresh);
      window.removeEventListener("salesbot:refresh-related", refresh);
    };
  }, []);

  const selected = batches?.find((batch) => String(batch.id) === selectedId);
  return <section className="outreach-batches" aria-labelledby={headingId}>
    <header className="outreach-batches-header">
      <h2 id={headingId}>外联批次</h2>
      <button type="button" className="outreach-batches-icon" title="刷新批次" aria-label="刷新批次"
        disabled={loading} onClick={() => setRevision((value) => value + 1)}><span aria-hidden="true">&#8635;</span></button>
    </header>
    {loading && <p className="outreach-batches-state" role="status">正在加载批次...</p>}
    {error && <div className="outreach-batches-state outreach-batches-error" role="alert">
      <span>批次加载失败：{error}</span>
      <button type="button" onClick={() => setRevision((value) => value + 1)}>重试</button>
    </div>}
    {!loading && !error && !batches?.length && <p className="outreach-batches-state" role="status">暂无外联批次</p>}
    {!!batches?.length && !error && <>
      <label className="outreach-batches-selector">批次 / 负责销售
        <select value={selectedId} disabled={loading} onChange={(event) => setSelectedId(event.target.value)}>
          {batches.map((batch) => <option key={batch.id} value={String(batch.id)}>
            {batch.name} / {batch.owner_name || "未分配"} / {batch.total_count ?? "未记录"} 位客户
          </option>)}
        </select>
      </label>
      {selected && <BatchAudit key={selectedId} listedBatch={selected} revision={revision} />}
    </>}
  </section>;
}

function BatchAudit({ listedBatch, revision }) {
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError("");
    const params = new URLSearchParams({ limit: String(pageSize), offset: String(offset), search: query });
    api(`/api/outreach-batches/${encodeURIComponent(listedBatch.id)}?${params}`, { signal: controller.signal }).then((result) => {
      if (controller.signal.aborted) return;
      if (!result.batch || !result.summary || !Array.isArray(result.contacts) || !Number.isInteger(result.total) || result.total < 0) {
        throw new Error("批次明细格式错误");
      }
      const lastOffset = Math.max(0, Math.ceil(result.total / pageSize) - 1) * pageSize;
      if (offset > lastOffset) {
        setOffset(lastOffset);
        return;
      }
      setData(result);
    }).catch((err) => {
      if (!controller.signal.aborted) setError(err.message);
    });
    return () => controller.abort();
  }, [listedBatch.id, offset, query, revision, retry]);

  const batch = data?.batch || listedBatch;
  const page = Math.floor(offset / pageSize) + 1;
  const pages = data ? Math.max(1, Math.ceil(data.total / pageSize)) : 1;
  return <>
    <div className="outreach-batches-meta">
      <strong>{batch.name}</strong>
      <span>负责销售：{batch.owner_name || "未分配"}</span>
      <span>地区：{batch.region || "未记录"}</span>
      <span>创建：{formatDate(listedBatch.created_at)}</span>
      <span>来源：{batch.source_ref || "未记录"}</span>
    </div>
    <form className="outreach-batches-search" role="search" aria-label="批次客户搜索" onSubmit={(event) => {
      event.preventDefault();
      setOffset(0);
      setQuery(search.trim());
    }}>
      <label>搜索批次客户
        <input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="姓名、公司、邮箱、职位、主题" />
      </label>
      <button type="submit">搜索</button>
    </form>
    {!data && !error && <p className="outreach-batches-state" role="status">正在加载批次明细...</p>}
    {error && <div className="outreach-batches-state outreach-batches-error" role="alert">
      <span>批次明细加载失败：{error}</span>
      <button type="button" onClick={() => setRetry((value) => value + 1)}>重试</button>
    </div>}
    {data && <>
      <dl className="outreach-batches-summary" aria-label="批次汇总">
        {summaryFields.map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{data.summary[key] ?? "未记录"}</dd></div>)}
      </dl>
      <div className="outreach-batches-results" role="status">
        {query ? `搜索“${query}”：` : ""}共 {data.total} 位客户
        {!!data.contacts.length && `，当前 ${offset + 1}-${offset + data.contacts.length} 位`}
      </div>
      {data.contacts.length ? <div className="outreach-batches-table-wrap" tabIndex={0} role="region" aria-label="批次客户明细">
        <table className="outreach-batches-table">
          <thead><tr>{["客户 / 收件人", "背调摘要", "最新邮件", "发送 / 反馈", "生命周期 / 下一步"].map((label) => <th key={label} scope="col">{label}</th>)}</tr></thead>
          <tbody>{data.contacts.map((contact) => <BatchContactRow key={contact.id} contact={contact} batchId={batch.id} />)}</tbody>
        </table>
      </div> : <p className="outreach-batches-state" role="status">{query ? "没有匹配的客户" : "此批次暂无客户"}</p>}
      <nav className="outreach-batches-pagination" aria-label="批次客户分页">
        <span>每页 {pageSize} 位客户</span>
        <div>
          <button type="button" className="outreach-batches-icon" title="上一页" aria-label="上一页" disabled={offset === 0}
            onClick={() => setOffset((value) => Math.max(0, value - pageSize))}><span aria-hidden="true">&larr;</span></button>
          <b aria-label={`第 ${page} 页，共 ${pages} 页`}>{page} / {pages}</b>
          <button type="button" className="outreach-batches-icon" title="下一页" aria-label="下一页" disabled={offset + pageSize >= data.total}
            onClick={() => setOffset((value) => value + pageSize)}><span aria-hidden="true">&rarr;</span></button>
        </div>
      </nav>
    </>}
  </>;
}

function BatchContactRow({ contact, batchId }) {
  const name = [contact.first_name, contact.last_name].filter(Boolean).join(" ") || contact.company_name || `客户 #${contact.id}`;
  const hasEmail = Boolean(contact.latest_subject?.trim() || contact.latest_body?.trim());
  return <tr>
    <td>
      <button type="button" className="outreach-batches-contact" onClick={() => openContactWorkspace(contact.id, 0, batchId)}>{name}</button>
      <div>{contact.company_name || "公司未记录"}</div>
      {contact.job_title && <div>{contact.job_title}</div>}
      <div className="outreach-batches-recipient">{contact.recipient_email || contact.email || "无收件邮箱"}</div>
      {contact.recipient_email && contact.email && contact.recipient_email !== contact.email && <div className="outreach-batches-muted">当前邮箱：{contact.email}</div>}
      <div className="outreach-batches-muted">负责销售：{contact.owner_name || "未分配"}</div>
      <div className="outreach-batches-muted">来源：{contact.source_ref || "未记录"}{contact.source_row != null && ` / 第 ${contact.source_row} 行`}</div>
    </td>
    <td>
      {contact.researched_at ? <>
        <div className="outreach-batches-muted">背调记录：{formatDate(contact.researched_at)}</div>
        <div className="outreach-batches-profile">{contact.research_summary?.trim() || "背调摘要未记录"}</div>
      </> : <>
        <div>尚未背调</div>
        {contact.profile_summary?.trim() && <>
          <div className="outreach-batches-muted">已有客户资料（非背调结论）</div>
          <div className="outreach-batches-profile">{contact.profile_summary}</div>
        </>}
      </>}
    </td>
    <td>{hasEmail ? <details className="outreach-batches-message">
      <summary>{contact.latest_subject || "无主题"}</summary>
      <dl>
        <dt>发件邮箱</dt><dd>{contact.sender_email || "未记录"}</dd>
        <dt>收件邮箱</dt><dd>{contact.recipient_email || contact.email || "未记录"}</dd>
        <dt>主题</dt><dd>{contact.latest_subject || "无主题"}</dd>
        <dt>正文</dt><dd>{contact.latest_body || "正文未记录"}</dd>
      </dl>
    </details> : <span className="outreach-batches-muted">暂无邮件内容</span>}</td>
    <td>
      <strong>{statusLabels[contact.last_message_status] || contact.last_message_status || (Number(contact.sent_count) > 0 ? "已发送" : "尚未发送")}</strong>
      <div>发送 {contact.sent_count ?? 0} / 回复 {contact.replied_count ?? 0} / 打开 {contact.opened_count ?? 0}</div>
      <div className="outreach-batches-muted">最近发送：{formatDate(contact.last_sent_at)}</div>
      {contact.eligibility_reason && <div className="outreach-batches-reason">发送资格：{eligibilityLabels[contact.eligibility_reason] || contact.eligibility_reason}</div>}
    </td>
    <td>
      <strong>{lifecycleLabels[contact.lifecycle_stage] || contact.lifecycle_stage || "阶段未记录"}</strong>
      <div className="outreach-batches-muted">客户状态：{statusLabels[contact.status] || contact.status || "未记录"}</div>
      <div className="outreach-batches-task">{contact.next_task_title || "暂无下一步任务"}</div>
      {contact.next_task_due_at && <div className="outreach-batches-muted">到期：{formatDate(contact.next_task_due_at)}</div>}
    </td>
  </tr>;
}

function formatDate(value) {
  return value ? String(value).replace("T", " ").slice(0, 16) : "未记录";
}
