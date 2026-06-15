import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "./api.js";

const tabs = [
  ["source", "自动获客"],
  ["company-seeds", "公司种子导入"],
  ["linkedin", "LinkedIn 公网搜索"],
  ["csv", "CSV 导入"],
  ["manual", "手动新增"],
  ["runbook", "批量处理"],
  ["status", "状态管理"],
];

export default function WorkbenchPortal() {
  const [target, setTarget] = useState(null);

  useEffect(() => {
    const node = document.querySelector("#react-workbench-root");
    const workbench = document.querySelector("#workbench");
    workbench?.classList.add("react-workbench-enabled");
    setTarget(node);
    return () => workbench?.classList.remove("react-workbench-enabled");
  }, []);

  if (!target) return null;
  return createPortal(<Workbench />, target);
}

function Workbench() {
  const [activeTab, setActiveTab] = useState("source");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  function notify(message, type = "") {
    setMessage(type ? "" : message);
    setError(type === "error" ? message : "");
    window.dispatchEvent(new CustomEvent("salesbot:notice", { detail: { message, type } }));
  }

  async function guarded(fn) {
    setMessage("");
    setError("");
    try {
      await fn();
    } catch (err) {
      notify(err.message, "error");
    }
  }

  return (
    <>
      <div className="tabs" role="tablist">
        {tabs.map(([id, label]) => (
          <button key={id} type="button" className={`tab ${activeTab === id ? "active" : ""}`} onClick={() => setActiveTab(id)}>
            {label}
          </button>
        ))}
      </div>
      {(message || error) && <div className={`admin-alert ${error ? "is-error" : ""}`}>{error || message}</div>}
      {activeTab === "source" && <SourcePanel guarded={guarded} notify={notify} />}
      {activeTab === "company-seeds" && <CompanySeedPanel guarded={guarded} notify={notify} />}
      {activeTab === "linkedin" && <LinkedInSearchPanel guarded={guarded} notify={notify} />}
      {activeTab === "csv" && <CsvPanel guarded={guarded} notify={notify} />}
      {activeTab === "manual" && <ManualPanel guarded={guarded} notify={notify} />}
      {activeTab === "runbook" && <RunbookPanel guarded={guarded} notify={notify} />}
      {activeTab === "status" && <StatusPanel guarded={guarded} notify={notify} />}
    </>
  );
}

function SourcePanel({ guarded, notify }) {
  const [form, setForm] = useState({ company_website: "", role: "", industry: "", location: "", limit: 1 });
  return (
    <div className="tab-panel active">
      <div className="helper"><strong>从 Prospeo 自动找目标客户</strong><p>推荐先用 Limit=1 测试。公司网站可选；填了公司网站会只找该公司的相关职位。</p></div>
      <div className="form-grid">
        <Field label="目标公司网站" value={form.company_website} onChange={(v) => setForm({ ...form, company_website: v })} placeholder="stripe.com，可选" />
        <Field label="目标职位" value={form.role} onChange={(v) => setForm({ ...form, role: v })} placeholder="Founder / VP of Engineering" />
        <Field label="行业" value={form.industry} onChange={(v) => setForm({ ...form, industry: v })} placeholder="SaaS，可选" />
        <Field label="地区" value={form.location} onChange={(v) => setForm({ ...form, location: v })} placeholder="United States，可选" />
        <Field label="数量" type="number" value={form.limit} onChange={(v) => setForm({ ...form, limit: v })} />
      </div>
      <div className="panel-actions">
        <button className="primary" type="button" onClick={() => guarded(async () => {
          if (!form.role.trim()) throw new Error("Role 必填");
          notify("正在调用 Prospeo 自动获客，Limit 越大等待越久...");
          const result = await api("/api/source", { method: "POST", body: JSON.stringify({ ...form, company_website: normalizeCompanyWebsite(form.company_website), limit: Number(form.limit || 1) }) });
          if (result.usage) window.dispatchEvent(new CustomEvent("salesbot:usage", { detail: { usage: result.usage } }));
          const outcome = result.result || [0, 0];
          notify(`自动获客完成：新增 ${outcome[0]} 条，重复 ${outcome[1]} 条`);
          refreshAll();
        })}>开始自动获客</button>
      </div>
    </div>
  );
}

function CompanySeedPanel({ guarded, notify }) {
  const [file, setFile] = useState(null);
  const [form, setForm] = useState({ default_location: "", default_industry: "", per_company_limit: 5, auto_queue: false, auto_send: false });
  return (
    <div className="tab-panel active">
      <div className="helper">
        <strong>导入公司/店铺种子表，自动找 LinkedIn 联系人</strong>
        <p>适合公司/店铺名称、类别、背调理由、官网/联系链接、职位这种表。系统会按公司和职位搜索公开 LinkedIn 主页，生成邮箱候选；只有 valid 工作邮箱才会入队或发送。</p>
      </div>
      <div className="helper subtle">
        <strong>推荐模板列</strong>
        <p>company_name, category, reason, website, job_titles, industry, location, phone, email。中文列名也支持：公司/店铺名称、类别、简短背调、官网/联系链接、职位、电话、邮箱。</p>
      </div>
      <div className="form-grid">
        <label>选择 CSV 文件<input type="file" accept=".csv,text/csv" onChange={(event) => setFile(event.target.files?.[0] || null)} /></label>
        <Field label="默认地区" value={form.default_location} onChange={(v) => setForm({ ...form, default_location: v })} placeholder="India / United States，可选" />
        <Field label="默认行业" value={form.default_industry} onChange={(v) => setForm({ ...form, default_industry: v })} placeholder="luxury / resale，可选" />
        <Field label="每家公司最多联系人" type="number" value={form.per_company_limit} onChange={(v) => setForm({ ...form, per_company_limit: v })} />
      </div>
      <div className="option-row">
        <Check label="找到 valid 邮箱后自动加入队列" checked={form.auto_queue} onChange={(v) => setForm({ ...form, auto_queue: v, auto_send: v ? form.auto_send : false })} />
        <Check label="入队后自动发送邮件" checked={form.auto_send} onChange={(v) => setForm({ ...form, auto_send: v, auto_queue: v ? true : form.auto_queue })} />
      </div>
      <div className="panel-actions">
        <button className="primary" type="button" onClick={() => guarded(async () => {
          if (!file) throw new Error("请选择公司种子 CSV 文件");
          const csv = await file.text();
          notify("正在导入公司种子，并通过 LinkedIn 公网搜索找联系人...");
          const response = await api("/api/import/company-seeds", {
            method: "POST",
            body: JSON.stringify({
              csv,
              default_location: form.default_location,
              default_industry: form.default_industry,
              per_company_limit: Number(form.per_company_limit || 5),
              auto_queue: form.auto_queue,
              auto_send: form.auto_send,
            }),
          });
          if (response.usage) window.dispatchEvent(new CustomEvent("salesbot:usage", { detail: { usage: response.usage } }));
          const result = response.result || {};
          notify(`公司种子导入完成：公司 ${response.parsed} 个，LinkedIn 结果 ${result.results || 0} 条，入库 ${result.promoted || 0} 条，电话挂载 ${result.phone_attached || 0} 条，入队 ${result.queued || 0} 条，发送 ${result.sent || 0} 封`);
          refreshAll();
        })}>导入并获客</button>
      </div>
    </div>
  );
}
function LinkedInSearchPanel({ guarded, notify }) {
  const [form, setForm] = useState({ role: "", industry: "", location: "", company_keyword: "", limit: 10, auto_domain_lookup: true, auto_generate_email_candidates: true, high_confidence_verify: true });
  const [tasks, setTasks] = useState([]);
  const [taskId, setTaskId] = useState(null);
  const [results, setResults] = useState([]);

  const loadTasks = useCallback(async () => {
    const data = await api("/api/search-tasks");
    const rows = data.tasks || [];
    setTasks(rows);
    const nextId = taskId || rows[0]?.id || null;
    setTaskId(nextId);
    if (nextId) {
      const resultData = await api(`/api/search-results?task_id=${encodeURIComponent(nextId)}`);
      setResults(resultData.results || []);
    }
  }, [taskId]);

  useEffect(() => {
    loadTasks().catch(() => {});
  }, [loadTasks]);

  return (
    <div className="tab-panel active">
      <div className="helper"><strong>从 Google 公开索引找 LinkedIn 个人主页</strong><p>不登录 LinkedIn，不抓后台页面；只解析公开搜索结果。第一版只入候选池和客户列表，不自动发信。</p></div>
      <div className="form-grid">
        <Field label="目标职位" value={form.role} onChange={(v) => setForm({ ...form, role: v })} placeholder="Brand Manager / Distributor / Founder" />
        <Field label="行业关键词" value={form.industry} onChange={(v) => setForm({ ...form, industry: v })} placeholder="luxury / watch / consumer electronics" />
        <Field label="地区" value={form.location} onChange={(v) => setForm({ ...form, location: v })} placeholder="United States / UAE / Singapore" />
        <Field label="公司关键词" value={form.company_keyword} onChange={(v) => setForm({ ...form, company_keyword: v })} placeholder="Hermes / Rolex，可选" />
        <Field label="数量" type="number" value={form.limit} onChange={(v) => setForm({ ...form, limit: v })} />
      </div>
      <div className="option-row">
        <Check label="自动补公司官网域名" checked={form.auto_domain_lookup} onChange={(v) => setForm({ ...form, auto_domain_lookup: v })} />
        <Check label="自动生成邮箱候选" checked={form.auto_generate_email_candidates} onChange={(v) => setForm({ ...form, auto_generate_email_candidates: v })} />
        <Check label="只验证高置信候选" checked={form.high_confidence_verify} onChange={(v) => setForm({ ...form, high_confidence_verify: v })} />
      </div>
      <div className="panel-actions">
        <button className="primary" type="button" onClick={() => guarded(async () => {
          if (!form.role && !form.industry && !form.company_keyword) throw new Error("至少填写职位、行业或公司关键词");
          notify("正在通过 Google 公开索引搜索 LinkedIn 个人主页...");
          const response = await api("/api/source/linkedin-public-search", { method: "POST", body: JSON.stringify({ ...form, limit: Number(form.limit || 10) }) });
          if (response.usage) window.dispatchEvent(new CustomEvent("salesbot:usage", { detail: { usage: response.usage } }));
          setTaskId(response.result.task_id);
          notify(`LinkedIn 公网搜索完成：解析 ${response.result.results} 条，入库 ${response.result.promoted} 条，跳过 ${response.result.skipped} 条`);
          refreshAll();
          await loadTasks();
        })}>开始公网搜索</button>
        <button type="button" onClick={() => guarded(loadTasks)}>刷新搜索结果</button>
      </div>
      <SearchResults tasks={tasks} taskId={taskId} results={results} setTaskId={setTaskId} setResults={setResults} notify={notify} guarded={guarded} />
    </div>
  );
}

function SearchResults({ tasks, taskId, results, setTaskId, setResults, notify, guarded }) {
  async function selectTask(id) {
    setTaskId(id);
    const data = await api(`/api/search-results?task_id=${encodeURIComponent(id)}`);
    setResults(data.results || []);
  }
  if (!tasks.length) return <div className="search-output"><div className="empty-state compact"><strong>还没有 LinkedIn 公网搜索任务</strong><p>填写上方条件后开始搜索，结果会先进入候选池和客户列表，不会自动发邮件。</p></div></div>;
  return (
    <div className="search-output">
      <div className="search-task-strip">
        {tasks.slice(0, 8).map((task) => <button key={task.id} type="button" className={Number(task.id) === Number(taskId) ? "active" : ""} onClick={() => guarded(() => selectTask(task.id))}><strong>#{task.id} {task.status}</strong><span>{searchTaskTitle(task)}</span><small>结果 {task.result_count || 0} · 入库 {task.promoted_count || 0}</small></button>)}
      </div>
      <div className="search-result-panel">
        {!results.length ? <div className="empty-state compact"><strong>该任务暂无结果</strong><p>如果 Google CSE 没有返回内容，可以放宽职位或地区关键词。</p></div> : (
          <div className="linkedin-results">{results.map((row) => <SearchResult key={row.id} row={row} onPromote={() => guarded(async () => {
            const result = await api("/api/search-results/promote", { method: "POST", body: JSON.stringify({ result_id: row.id }) });
            notify(result.contact_id ? `已入库联系人 #${result.contact_id}` : "已处理，可能是重复客户");
            refreshAll();
            await selectTask(taskId);
          })} />)}</div>
        )}
      </div>
    </div>
  );
}

function SearchResult({ row, onPromote }) {
  return (
    <article className={`linkedin-result ${row.status || ""}`}>
      <header><div><strong>{fullName(row) || row.raw_title || "未解析姓名"}</strong><span>{row.job_title || "职位待确认"} · {row.company_name || "公司待确认"}</span></div><b>{Number(row.lead_score || 0)}</b></header>
      <p>{row.raw_snippet || ""}</p>
      <div className="result-meta"><span>{row.company_domain || "域名待补"}</span><span>{row.location || "地区待确认"}</span><span>{searchResultStatus(row.status)}</span>{row.failure_reason && <span className="danger-text">{row.failure_reason}</span>}</div>
      <div className="result-candidates">{renderInlineEmailCandidates(row.email_candidates || [])}</div>
      <footer><a href={row.linkedin_url || row.raw_url || "#"} target="_blank" rel="noreferrer">打开 LinkedIn</a>{row.promoted_contact_id ? <span>已入库 #{row.promoted_contact_id}</span> : <button type="button" onClick={onPromote}>入库</button>}</footer>
    </article>
  );
}

function CsvPanel({ guarded, notify }) {
  const [file, setFile] = useState(null);
  return <div className="tab-panel active"><div className="helper"><strong>导入员工已有的线索表</strong><p>CSV 不要求 LinkedIn URL；有邮箱会直接进入“已富化”，没有邮箱后续可点“富化”。</p></div><div className="form-grid compact"><label>选择 CSV 文件<input type="file" accept=".csv,text/csv" onChange={(event) => setFile(event.target.files?.[0] || null)} /></label></div><div className="panel-actions"><button className="primary" type="button" onClick={() => guarded(async () => { if (!file) throw new Error("请选择 CSV 文件"); const csv = await file.text(); const result = await api("/api/import/csv", { method: "POST", body: JSON.stringify({ csv, source: `csv:${file.name}` }) }); notify(`CSV 导入完成：解析 ${result.parsed} 条，新增 ${result.inserted} 条，重复 ${result.skipped} 条`); refreshAll(); })}>导入 CSV</button></div></div>;
}

function ManualPanel({ guarded, notify }) {
  const [form, setForm] = useState({ linkedin_url: "", first_name: "", last_name: "", email: "", job_title: "", company_name: "", company_domain: "" });
  return <div className="tab-panel active"><div className="helper"><strong>临时新增一个客户</strong><p>适合测试流程，或把销售刚找到的单个客户快速录入系统。</p></div><div className="form-grid"><Field label="LinkedIn URL" value={form.linkedin_url} onChange={(v) => setForm({ ...form, linkedin_url: v })} placeholder="https://linkedin.com/in/..." /><Field label="名" value={form.first_name} onChange={(v) => setForm({ ...form, first_name: v })} placeholder="Ada" /><Field label="姓" value={form.last_name} onChange={(v) => setForm({ ...form, last_name: v })} placeholder="Lovelace" /><Field label="邮箱" type="email" value={form.email} onChange={(v) => setForm({ ...form, email: v })} placeholder="ada@example.com" /><Field label="职位" value={form.job_title} onChange={(v) => setForm({ ...form, job_title: v })} placeholder="Founder" /><Field label="公司" value={form.company_name} onChange={(v) => setForm({ ...form, company_name: v })} placeholder="Example Inc" /><Field label="公司域名" value={form.company_domain} onChange={(v) => setForm({ ...form, company_domain: v })} placeholder="example.com" /></div><div className="panel-actions"><button className="primary" type="button" onClick={() => guarded(async () => { if (!form.linkedin_url) throw new Error("LinkedIn URL 必填"); const payload = { ...form, email: form.email || null, email_status: form.email ? "valid" : "unknown", status: form.email ? "enriched" : "new", source: "manual_dashboard" }; const result = await api("/api/contacts", { method: "POST", body: JSON.stringify(payload) }); notify(`新增完成：${JSON.stringify(result)}`); refreshAll(); })}>新增联系人</button></div></div>;
}

function RunbookPanel({ guarded, notify }) {
  const actions = [["migrate", "初始化/迁移数据库"], ["enrich", "富化邮箱（最多 100 条）"], ["social-enrich", "富化社媒（最多 100 条）"], ["queue", "加入队列（最多 100 条）"], ["send", "发送/演练（最多 100 封）"], ["scheduler", "跑一轮调度"]];
  return <div className="tab-panel active"><div className="helper"><strong>按顺序跑批量流程</strong><p>一般顺序是：富化 -&gt; 加入队列 -&gt; 发送演练/真实发送。也可以点“跑一轮调度”自动执行一遍。</p></div><div className="action-grid">{actions.map(([action, label]) => <button key={action} type="button" className={action === "scheduler" ? "primary" : ""} onClick={() => guarded(async () => { const data = await api(`/api/${action}`, { method: "POST", body: JSON.stringify({ limit: 100 }) }); if (data.usage) window.dispatchEvent(new CustomEvent("salesbot:usage", { detail: { usage: data.usage } })); notify(`${label} 完成：${JSON.stringify(data)}`); refreshAll(); })}>{label}</button>)}</div></div>;
}

function StatusPanel({ guarded, notify }) {
  const [contactId, setContactId] = useState("");
  const [status, setStatus] = useState("new");
  const [email, setEmail] = useState("");
  const [domain, setDomain] = useState("");
  return <div className="tab-panel active"><div className="helper"><strong>人工修正状态或加入黑名单</strong><p>客户已回复、退订、退信，或者某个邮箱/域名永远不应外联时使用。</p></div><div className="form-grid"><Field label="联系人 ID" type="number" value={contactId} onChange={setContactId} placeholder="1" /><label>Status<select value={status} onChange={(event) => setStatus(event.target.value)}>{["new", "enriched", "queued", "sent_1", "sent_2", "sent_3", "replied", "bounced", "unsubscribed"].map((item) => <option key={item}>{item}</option>)}</select></label><Field label="黑名单邮箱" type="email" value={email} onChange={setEmail} placeholder="name@example.com" /><Field label="黑名单域名" value={domain} onChange={setDomain} placeholder="example.com" /></div><div className="panel-actions"><button type="button" onClick={() => guarded(async () => { await api("/api/mark", { method: "POST", body: JSON.stringify({ contact_id: Number(contactId), status }) }); notify("状态已更新"); refreshAll(); })}>更新状态</button><button type="button" onClick={() => guarded(async () => { await api("/api/blacklist", { method: "POST", body: JSON.stringify({ email: email || null, domain: domain || null, reason: "dashboard" }) }); notify("黑名单已更新"); refreshAll(); })}>加入黑名单</button></div></div>;
}

function Field({ label, value, onChange, type = "text", placeholder = "" }) {
  return <label>{label}<input type={type} value={value} min={type === "number" ? "1" : undefined} max={type === "number" ? "100" : undefined} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} /></label>;
}

function Check({ label, checked, onChange }) {
  return <label><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /> {label}</label>;
}

function refreshAll() {
  window.dispatchEvent(new CustomEvent("salesbot:contacts-refresh"));
  window.dispatchEvent(new CustomEvent("salesbot:refresh-related"));
  window.dispatchEvent(new CustomEvent("salesbot:ops-refresh"));
}

function normalizeCompanyWebsite(value) {
  return String(value || "").trim().replace(/^https?:\/\//i, "").replace(/^www\./i, "").split("/")[0];
}

function fullName(row) {
  return [row.first_name, row.last_name].filter(Boolean).join(" ");
}

function searchTaskTitle(task) {
  const criteria = task.criteria || {};
  return [criteria.role || criteria.title, criteria.industry, criteria.location, criteria.company_keyword].filter(Boolean).join(" / ") || "公开搜索";
}

function searchResultStatus(status) {
  return { candidate: "候选", low_score: "低分跳过", promoted: "已入库", duplicate: "重复", failed: "失败" }[status] || status || "未知";
}

function renderInlineEmailCandidates(candidates) {
  if (!Array.isArray(candidates) || !candidates.length) return <span className="muted">暂无邮箱候选</span>;
  return candidates.slice(0, 4).map((item) => <span key={item.email} className={`candidate-chip ${item.category || ""}`}>{item.email || ""}<small>{Number(item.confidence || 0)}%</small></span>);
}
