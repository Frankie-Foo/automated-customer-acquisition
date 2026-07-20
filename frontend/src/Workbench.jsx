import { useCallback, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "./api.js";

const primaryTabs = [
  ["company-seeds", "批量导入"],
  ["linkedin", "精确找人"],
  ["source", "条件搜索"],
  ["csv", "CSV 导入"],
  ["manual", "单个录入"],
];

const advancedTabs = [
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
  const [activeTab, setActiveTab] = useState("company-seeds");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [user, setUser] = useState(() => window.SALESBOT_SESSION?.user || null);
  const isAdmin = user?.role === "admin";

  useEffect(() => {
    const handleSession = (event) => setUser(event.detail?.user || null);
    window.addEventListener("salesbot:session", handleSession);
    return () => window.removeEventListener("salesbot:session", handleSession);
  }, []);

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
      <div className="workbench-nav">
        <div className="tabs" role="tablist">
          {primaryTabs.map(([id, label]) => (
            <button key={id} type="button" className={`tab ${activeTab === id ? "active" : ""}`} onClick={() => setActiveTab(id)}>
              {label}
            </button>
          ))}
        </div>
        <details className="advanced-tools">
          <summary>更多工具</summary>
          <div className="advanced-menu">
            {advancedTabs.map(([id, label]) => <button key={id} type="button" className={activeTab === id ? "active" : ""} onClick={() => setActiveTab(id)}>{label}</button>)}
          </div>
        </details>
      </div>
      {(message || error) && <div className={`admin-alert ${error ? "is-error" : ""}`}>{error || message}</div>}
      {activeTab === "source" && <SourcePanel guarded={guarded} notify={notify} />}
      {activeTab === "company-seeds" && <CompanySeedPanelV2 guarded={guarded} notify={notify} />}
      {activeTab === "linkedin" && <LinkedInSearchPanel guarded={guarded} notify={notify} />}
      {activeTab === "csv" && <CsvPanel guarded={guarded} notify={notify} />}
      {activeTab === "manual" && <ManualPanel guarded={guarded} notify={notify} />}
      {activeTab === "runbook" && <RunbookPanel guarded={guarded} notify={notify} isAdmin={isAdmin} />}
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

function CompanySeedPanelV2({ guarded, notify }) {
  const [file, setFile] = useState(null);
  const [form, setForm] = useState({ default_location: "", default_industry: "", per_company_limit: 5, auto_prepare_drafts: true });
  const [runs, setRuns] = useState([]);
  const [working, setWorking] = useState(false);
  const regionMode = regionModeLabel(form.default_location);

  const loadRuns = useCallback(async () => {
    const response = await api("/api/automation-runs");
    setRuns(response.runs || []);
  }, []);

  useEffect(() => {
    loadRuns().catch(() => {});
    const timer = window.setInterval(() => {
      if (runs.some((run) => ["queued", "running"].includes(run.status))) loadRuns().catch(() => {});
    }, 2500);
    return () => window.clearInterval(timer);
  }, [loadRuns, runs]);

  async function submitImport() {
    if (!file) throw new Error("请选择 Excel 或 CSV 文件");
    setWorking(true);
    try {
      notify("正在创建后台获客任务，上传完成后可以离开页面...");
      const fileBase64 = await readFileBase64(file);
      const response = await api("/api/automation-runs/company-seeds", {
        method: "POST",
        body: JSON.stringify({
          filename: file.name,
          file_base64: fileBase64,
          default_location: form.default_location,
          default_industry: form.default_industry,
          per_company_limit: Number(form.per_company_limit || 5),
          auto_send: false,
          auto_prepare_drafts: form.auto_prepare_drafts,
          idempotency_key: window.crypto?.randomUUID?.() || `upload-${Date.now()}`,
        }),
      });
      notify(`任务 #${response.run?.id} 已创建，共 ${response.parsed || 0} 家公司。系统会在后台逐家处理。`);
      await loadRuns();
      refreshAll();
    } finally {
      setWorking(false);
    }
  }

  return (
    <div className="tab-panel active">
      <div className={`region-mode-strip ${regionMode.key}`}>
        <strong>{regionMode.label}</strong>
        <span>{regionMode.description}</span>
      </div>
      <div className="helper">
        <strong>批量获客导入</strong>
        <p>销售直接上传 Excel/CSV。系统会解析公司、官网、职位、电话、邮箱，自动找公开 LinkedIn 联系人，并把结果分成“去重可发送”和“需复核候选”。</p>
      </div>
      <div className="helper subtle">
        <strong>推荐表头</strong>
        <p>company_name, category, reason, website, job_titles, industry, location, phone, email。中文也支持：公司/店铺名称、类别、简短背调、官网/联系链接、职位、地区、电话、邮箱。</p>
      </div>
      <div className="form-grid">
        <label>上传 Excel/CSV
          <input type="file" accept=".xlsx,.xlsm,.csv,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={(event) => setFile(event.target.files?.[0] || null)} />
          {file && <small>{file.name}</small>}
        </label>
        <Field label="默认地区" value={form.default_location} onChange={(v) => setForm({ ...form, default_location: v })} placeholder="India / UAE / Russia，可选" />
        <Field label="默认行业" value={form.default_industry} onChange={(v) => setForm({ ...form, default_industry: v })} placeholder="luxury / watch / hotel，可选" />
        <Field label="每家公司最多联系人" type="number" value={form.per_company_limit} onChange={(v) => setForm({ ...form, per_company_limit: v })} />
      </div>
      <div className="option-row">
        <Check label="分配后自动准备客户画像和待审核邮件草稿" checked={form.auto_prepare_drafts} onChange={(v) => setForm({ ...form, auto_prepare_drafts: v })} />
        <span className="safe-flow-note">导入不会自动发信。请到“核验客户”检查结果，再到“邮件触达”确认内容。</span>
      </div>
      <div className="panel-actions">
        <button className="primary" type="button" disabled={working} onClick={() => guarded(submitImport)}>
          {working ? "处理中..." : "上传并开始获客"}
        </button>
        <button type="button" onClick={() => downloadCompanySeedTemplate()}>下载导入模板</button>
      </div>
      <AutomationRuns runs={runs} guarded={guarded} notify={notify} reload={loadRuns} />
    </div>
  );
}

function AutomationRuns({ runs, guarded, notify, reload }) {
  if (!runs.length) return null;

  async function act(run, action) {
    await api("/api/automation-runs/action", {
      method: "POST",
      body: JSON.stringify({ run_id: run.id, action }),
    });
    notify(action === "pause" ? `任务 #${run.id} 正在暂停` : `任务 #${run.id} 已继续`);
    await reload();
  }

  function openReview() {
    window.location.hash = "research";
    window.setTimeout(() => {
      window.dispatchEvent(new CustomEvent("salesbot:contact-filter", { detail: { filter: "mine" } }));
      window.dispatchEvent(new CustomEvent("salesbot:contacts-refresh"));
    }, 50);
  }

  return (
    <section className="automation-runs">
      <header><div><span className="eyebrow">Background tasks</span><h3>批量获客任务</h3></div><button type="button" onClick={() => guarded(reload)}>刷新</button></header>
      <div className="automation-run-list">
        {runs.slice(0, 8).map((run) => {
          const current = Number(run.progress_current || 0);
          const total = Number(run.progress_total || 0);
          const percent = total ? Math.round((current / total) * 100) : 0;
          const result = run.result || {};
          const assignment = result.assignment || {};
          const promoted = Number(result.promoted || 0);
          const drafted = Number(result.drafted || 0);
          return (
            <article key={run.id} className={`automation-run ${run.status}`}>
              <div className="automation-run-title"><strong>#{run.id} 公司批量获客</strong><span>{automationStatus(run.status)}</span></div>
              <div className="automation-progress"><span style={{ width: `${percent}%` }} /></div>
              <div className="automation-run-stats"><span>{current}/{total} 家</span><span>待核验 {promoted}</span><span>跳过 {result.skipped || 0}</span><span>画像 {result.profiled || 0}</span><span>可发送草稿 {drafted}</span><span>{percent}%</span></div>
              {assignment.owner && <p className="automation-run-note">已归属：<strong>{assignment.owner}</strong>，可在“我的客户”中核验。</p>}
              {run.status === "awaiting_approval" && promoted > 0 && drafted === 0 && <p className="automation-run-note is-warning">尚未找到已验证邮箱，需先核对身份并补齐邮箱，系统不会直接发送。</p>}
              {run.error && <p className="error-text">{run.error}</p>}
              <footer>
                {run.status === "running" && <button type="button" onClick={() => guarded(() => act(run, "pause"))}>暂停</button>}
                {["paused", "failed"].includes(run.status) && <button type="button" className="primary soft" onClick={() => guarded(() => act(run, run.status === "failed" ? "retry" : "resume"))}>{run.status === "failed" ? "重试" : "继续"}</button>}
                {run.status === "awaiting_approval" && <><button type="button" onClick={openReview}>核验 {promoted} 个客户</button>{drafted > 0 && <a className="primary-link" href="#outreach">去审核邮件</a>}</>}
              </footer>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function automationStatus(status) {
  return {
    queued: "排队中",
    running: "处理中",
    paused: "已暂停",
    failed: "失败",
    awaiting_approval: "等待核验",
    completed: "已完成",
  }[status] || status;
}

function BatchImportResult({ batch }) {
  if (!batch) return null;
  const result = batch.result || {};
  const report = batch.batch_report || {};
  const summary = report.summary || {};
  return (
    <section className="batch-result">
      <header>
        <div>
          <span className="eyebrow">Import result</span>
          <h3>本次导入结果</h3>
        </div>
        <div className="import-total-strip">
          <MetricChip label="公司" value={batch.parsed || 0} />
          <MetricChip label="LinkedIn结果" value={result.results || 0} />
          <MetricChip label="入库联系人" value={result.promoted || 0} />
          <MetricChip label="去重可发送" value={summary.sendable || 0} />
          <MetricChip label="需复核" value={summary.review || 0} />
        </div>
      </header>
      <ResultTable
        title="去重可发送清单"
        hint="这些邮箱已按 email 去重，后续发邮件只看这张。"
        rows={report.sendable || []}
        columns={[
          ["email", "邮箱"],
          ["name", "联系人"],
          ["job_title", "职位"],
          ["company_name", "公司"],
          ["company_domain", "域名"],
          ["email_source", "来源"],
          ["email_confidence", "置信度"],
          ["status", "状态"],
        ]}
        empty="本次没有拿到 valid 邮箱"
      />
      <ResultTable
        title="需复核候选"
        hint="这类邮箱可能是 accept_all、未验证或公司通用邮箱，不建议直接批量发。"
        rows={report.review || []}
        columns={[
          ["email", "候选邮箱"],
          ["name", "联系人"],
          ["job_title", "职位"],
          ["company_name", "公司"],
          ["category", "类型"],
          ["candidate_status", "状态"],
          ["confidence", "分数"],
          ["risk", "风险"],
        ]}
        empty="没有需要复核的候选邮箱"
      />
    </section>
  );
}

function ResultTable({ title, hint, rows, columns, empty }) {
  return (
    <div className="result-table-card">
      <div className="result-table-head"><strong>{title}</strong><span>{hint}</span></div>
      <div className="table-shell compact">
        <table>
          <thead><tr>{columns.map(([, label]) => <th key={label}>{label}</th>)}</tr></thead>
          <tbody>
            {rows.length ? rows.map((row, index) => (
              <tr key={`${row.email || row.contact_id || index}-${index}`}>
                {columns.map(([key]) => <td key={key}>{formatResultCell(row[key])}</td>)}
              </tr>
            )) : <tr><td colSpan={columns.length}><div className="empty-state compact">{empty}</div></td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function formatResultCell(value) {
  if (Array.isArray(value)) return value.filter(Boolean).join(", ");
  if (value === null || value === undefined || value === "") return "--";
  return String(value);
}
function LinkedInSearchPanel({ guarded, notify }) {
  const [form, setForm] = useState({ full_name: "", company_website: "", role: "", industry: "", location: "", company_keyword: "", limit: 10, auto_domain_lookup: true, auto_generate_email_candidates: true, high_confidence_verify: true });
  const [tasks, setTasks] = useState([]);
  const [taskId, setTaskId] = useState(null);
  const [results, setResults] = useState([]);
  const selectedTaskRef = useRef(null);

  useEffect(() => {
    selectedTaskRef.current = taskId;
  }, [taskId]);

  const loadTasks = useCallback(async (preferredTaskId = null) => {
    const data = await api("/api/search-tasks");
    const rows = data.tasks || [];
    setTasks(rows);
    const requestedId = preferredTaskId || selectedTaskRef.current;
    const nextId = rows.some((row) => Number(row.id) === Number(requestedId)) ? requestedId : rows[0]?.id || null;
    selectedTaskRef.current = nextId;
    setTaskId(nextId);
    if (nextId) {
      const resultData = await api(`/api/search-results?task_id=${encodeURIComponent(nextId)}`);
      setResults(resultData.results || []);
    } else {
      setResults([]);
    }
  }, []);

  useEffect(() => {
    loadTasks().catch(() => {});
  }, [loadTasks]);

  return (
    <div className="tab-panel active">
      <div className="helper"><strong>按姓名和公司精确匹配 LinkedIn 个人主页</strong><p>姓名和公司官网用于身份核验；系统同时比较职位、国家和行业，并展示每项匹配证据。低分候选不会自动进入正式客户。</p></div>
      <div className="form-grid">
        <Field label="联系人姓名" value={form.full_name} onChange={(v) => setForm({ ...form, full_name: v })} placeholder="例如 Carlos Rodriguez" />
        <Field label="公司官网" value={form.company_website} onChange={(v) => setForm({ ...form, company_website: v })} placeholder="example.com" />
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
          if (!form.full_name && !form.role && !form.industry && !form.company_keyword) throw new Error("至少填写姓名、职位、行业或公司关键词");
          if (form.full_name && !form.company_website && !form.company_keyword) throw new Error("精确找人时请填写公司官网或公司名称");
          notify("正在通过 Google 公开索引搜索 LinkedIn 个人主页...");
          const response = await api("/api/source/linkedin-public-search", { method: "POST", body: JSON.stringify({ ...form, limit: Number(form.limit || 10) }) });
          if (response.usage) window.dispatchEvent(new CustomEvent("salesbot:usage", { detail: { usage: response.usage } }));
          selectedTaskRef.current = response.result.task_id;
          setTaskId(response.result.task_id);
          notify(`LinkedIn 公网搜索完成：解析 ${response.result.results} 条，入库 ${response.result.promoted} 条，跳过 ${response.result.skipped} 条`);
          refreshAll();
          await loadTasks(response.result.task_id);
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
  const currentTask = tasks.find((task) => Number(task.id) === Number(taskId));
  return (
    <div className="search-output">
      <div className="search-output-head"><strong>搜索记录（点击切换）</strong><span>当前固定展示 #{taskId || "--"} 的候选客户，刷新不会自动跳到其他任务。</span></div>
      <div className="search-task-strip">
        {tasks.slice(0, 8).map((task) => <button key={task.id} type="button" className={Number(task.id) === Number(taskId) ? "active" : ""} onClick={() => guarded(() => selectTask(task.id))}><strong>#{task.id} {automationStatus(task.status)}</strong><span>{searchTaskTitle(task)}</span><small>结果 {task.result_count || 0} · 入库 {task.promoted_count || 0}</small></button>)}
      </div>
      <div className="search-result-panel">
        {currentTask && <div className="search-result-title"><strong>#{currentTask.id} 的候选客户</strong><span>{searchTaskTitle(currentTask)} · 共 {results.length} 条</span></div>}
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
  const evidence = Array.isArray(row.match_evidence) ? row.match_evidence : [];
  return (
    <article className={`linkedin-result ${row.status || ""}`}>
      <header><div><strong>{fullName(row) || row.raw_title || "未解析姓名"}</strong><span>{row.job_title || "职位待确认"} · {row.company_name || "公司待确认"}</span></div><div className="identity-score"><b>{Number(row.match_confidence ?? row.lead_score ?? 0)}</b><span>{identityStatusLabel(row.match_status)}</span></div></header>
      <p>{row.raw_snippet || ""}</p>
      {!!evidence.length && <div className="match-evidence">{evidence.map((item) => <span key={item.field} className={item.matched ? "matched" : "missed"}>{matchFieldLabel(item.field)} {item.matched ? "匹配" : "未匹配"}</span>)}</div>}
      <div className="result-meta"><span>{row.company_domain || "域名待补"}</span><span>{row.location || "地区待确认"}</span><span>{searchResultStatus(row.status)}</span>{row.failure_reason && <span className="danger-text">{row.failure_reason}</span>}</div>
      <div className="result-candidates">{renderInlineEmailCandidates(row.email_candidates || [])}</div>
      <footer><a href={row.linkedin_url || row.raw_url || "#"} target="_blank" rel="noreferrer">打开 LinkedIn</a>{row.promoted_contact_id ? <span>已入库 #{row.promoted_contact_id}</span> : <button type="button" onClick={onPromote}>入库</button>}</footer>
    </article>
  );
}

function identityStatusLabel(value) {
  return { confirmed: "已确认", likely: "较可能", review: "需复核", mismatch: "不匹配" }[value] || "需复核";
}

function matchFieldLabel(value) {
  return { name: "姓名", company: "公司", company_domain: "官网", title: "职位", location: "国家", industry: "行业" }[value] || value;
}

function CsvPanel({ guarded, notify }) {
  const [file, setFile] = useState(null);
  return <div className="tab-panel active"><div className="helper"><strong>导入员工已有的线索表</strong><p>CSV 不要求 LinkedIn URL；有邮箱会直接进入“已富化”，没有邮箱后续可点“富化”。</p></div><div className="form-grid compact"><label>选择 CSV 文件<input type="file" accept=".csv,text/csv" onChange={(event) => setFile(event.target.files?.[0] || null)} /></label></div><div className="panel-actions"><button className="primary" type="button" onClick={() => guarded(async () => { if (!file) throw new Error("请选择 CSV 文件"); const csv = await file.text(); const result = await api("/api/import/csv", { method: "POST", body: JSON.stringify({ csv, source: `csv:${file.name}` }) }); notify(`CSV 导入完成：解析 ${result.parsed} 条，新增 ${result.inserted} 条，重复 ${result.skipped} 条`); refreshAll(); })}>导入 CSV</button></div></div>;
}

function ManualPanel({ guarded, notify }) {
  const [form, setForm] = useState({ linkedin_url: "", first_name: "", last_name: "", email: "", job_title: "", company_name: "", company_domain: "", location: "", industry: "" });
  return <div className="tab-panel active"><div className="helper"><strong>临时新增一个客户</strong><p>LinkedIn URL 不是必填。至少提供邮箱，或提供姓名加公司信息，后续再补 LinkedIn 和邮箱。</p></div><div className="form-grid"><Field label="LinkedIn URL（可选）" value={form.linkedin_url} onChange={(v) => setForm({ ...form, linkedin_url: v })} placeholder="https://linkedin.com/in/..." /><Field label="名" value={form.first_name} onChange={(v) => setForm({ ...form, first_name: v })} placeholder="Ada" /><Field label="姓" value={form.last_name} onChange={(v) => setForm({ ...form, last_name: v })} placeholder="Lovelace" /><Field label="邮箱" type="email" value={form.email} onChange={(v) => setForm({ ...form, email: v })} placeholder="ada@example.com" /><Field label="职位" value={form.job_title} onChange={(v) => setForm({ ...form, job_title: v })} placeholder="Founder" /><Field label="公司" value={form.company_name} onChange={(v) => setForm({ ...form, company_name: v })} placeholder="Example Inc" /><Field label="公司官网" value={form.company_domain} onChange={(v) => setForm({ ...form, company_domain: v })} placeholder="example.com" /><Field label="国家/地区" value={form.location} onChange={(v) => setForm({ ...form, location: v })} placeholder="United Arab Emirates" /><Field label="行业" value={form.industry} onChange={(v) => setForm({ ...form, industry: v })} placeholder="Luxury retail" /></div><div className="panel-actions"><button className="primary" type="button" onClick={() => guarded(async () => { const hasCompanyIdentity = form.first_name.trim() && (form.company_name.trim() || form.company_domain.trim()); if (!form.linkedin_url.trim() && !form.email.trim() && !hasCompanyIdentity) throw new Error("请填写邮箱，或填写姓名加公司信息"); const payload = { ...form, linkedin_url: form.linkedin_url || null, email: form.email || null, email_status: form.email ? "valid" : "unknown", status: form.email ? "enriched" : "new", source: "manual_dashboard" }; const result = await api("/api/contacts", { method: "POST", body: JSON.stringify(payload) }); notify(`联系人已新增${result.inserted ? ` ${result.inserted} 条` : ""}${result.skipped ? `，跳过重复 ${result.skipped} 条` : ""}`); refreshAll(); })}>新增联系人</button></div></div>;
}

function RunbookPanel({ guarded, notify, isAdmin }) {
  const actions = [
    ["migrate", "初始化/迁移数据库", true],
    ["enrich", "富化邮箱（最多 100 条）"],
    ["social-enrich", "富化社媒（最多 100 条）"],
    ["queue", "加入队列（最多 100 条）"],
    ["send", "发送/演练（最多 100 封）", true],
    ["scheduler", "跑一轮调度", true],
  ].filter(([, , adminOnly]) => !adminOnly || isAdmin);
  return <div className="tab-panel active"><div className="helper"><strong>按顺序跑批量流程</strong><p>一般顺序是：富化 -&gt; 加入队列 -&gt; 发送演练/真实发送。批量发送前必须再次确认。</p></div><div className="action-grid">{actions.map(([action, label]) => <button key={action} type="button" className={action === "scheduler" ? "primary" : ""} onClick={() => guarded(async () => { if ((action === "send" || action === "scheduler") && !window.confirm(`${label} 可能触发真实邮件，确认继续？`)) return; const data = await api(`/api/${action}`, { method: "POST", body: JSON.stringify({ limit: 100 }) }); if (data.usage) window.dispatchEvent(new CustomEvent("salesbot:usage", { detail: { usage: data.usage } })); notify(`${label} 完成：${JSON.stringify(data)}`); refreshAll(); })}>{label}</button>)}</div></div>;
}

function StatusPanel({ guarded, notify }) {
  const [contactId, setContactId] = useState("");
  const [status, setStatus] = useState("new");
  const [email, setEmail] = useState("");
  const [domain, setDomain] = useState("");
  return <div className="tab-panel active"><div className="helper"><strong>人工修正状态或加入黑名单</strong><p>客户已回复、退订、退信，或者某个邮箱/域名永远不应外联时使用。</p></div><div className="form-grid"><Field label="联系人 ID" type="number" value={contactId} onChange={setContactId} placeholder="1" /><label>Status<select value={status} onChange={(event) => setStatus(event.target.value)}>{["new", "enriched", "queued", "sent_1", "sent_2", "sent_3", "replied", "bounced", "unsubscribed"].map((item) => <option key={item}>{item}</option>)}</select></label><Field label="黑名单邮箱" type="email" value={email} onChange={setEmail} placeholder="name@example.com" /><Field label="黑名单域名" value={domain} onChange={setDomain} placeholder="example.com" /></div><div className="panel-actions"><button type="button" onClick={() => guarded(async () => { await api("/api/mark", { method: "POST", body: JSON.stringify({ contact_id: Number(contactId), status }) }); notify("状态已更新"); refreshAll(); })}>更新状态</button><button type="button" onClick={() => guarded(async () => { await api("/api/blacklist", { method: "POST", body: JSON.stringify({ email: email || null, domain: domain || null, reason: "dashboard" }) }); notify("黑名单已更新"); refreshAll(); })}>加入黑名单</button></div></div>;
}

function MetricChip({ label, value }) {
  return <span className="metric-chip"><b>{Number(value || 0)}</b><em>{label}</em></span>;
}

function readFileBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("文件读取失败"));
    reader.onload = () => {
      const value = String(reader.result || "");
      resolve(value.includes(",") ? value.split(",", 2)[1] : value);
    };
    reader.readAsDataURL(file);
  });
}

function downloadCompanySeedTemplate() {
  const headers = ["company_name", "category", "reason", "website", "job_titles", "industry", "location", "phone", "email"];
  const example = ["Luxepolis", "二手奢侈品平台", "印度首屈一指的二手奢侈品电商，有门店和高端客户基础。", "luxepolis.com", "founder, owner, partner, VP, director, head", "luxury resale", "India", "", ""];
  const csv = [headers, example].map((row) => row.map(csvEscape).join(",")).join("\n");
  const blob = new Blob([`\ufeff${csv}`], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "company_seed_import_template.csv";
  link.click();
  URL.revokeObjectURL(url);
}

function csvEscape(value) {
  const text = String(value ?? "");
  return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function Field({ label, value, onChange, type = "text", placeholder = "" }) {
  const id = useId();
  return <label htmlFor={id}>{label}<input id={id} name={id} type={type} value={value} min={type === "number" ? "1" : undefined} max={type === "number" ? "100" : undefined} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} /></label>;
}

function Check({ label, checked, onChange }) {
  const id = useId();
  return <label htmlFor={id}><input id={id} name={id} type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /> {label}</label>;
}

function refreshAll() {
  window.dispatchEvent(new CustomEvent("salesbot:contacts-refresh"));
  window.dispatchEvent(new CustomEvent("salesbot:refresh-related"));
  window.dispatchEvent(new CustomEvent("salesbot:ops-refresh"));
}

function normalizeCompanyWebsite(value) {
  return String(value || "").trim().replace(/^https?:\/\//i, "").replace(/^www\./i, "").split("/")[0];
}

function regionModeLabel(location) {
  const value = String(location || "").toLowerCase();
  if (/(uae|dubai|abu dhabi|saudi|ksa|riyadh|jeddah|qatar|doha|kuwait|bahrain|oman|muscat|iraq|iran|jordan|lebanon|egypt|middle east|mena|gcc|中东)/.test(value)) {
    return { key: "mena", label: "中东增强模式", description: "自动使用英语/阿拉伯语搜索，并补充官网、WhatsApp、Instagram、电话和公开邮箱。" };
  }
  if (/(kazakhstan|almaty|astana|uzbekistan|tashkent|kyrgyzstan|bishkek|tajikistan|turkmenistan|azerbaijan|armenia|georgia|central asia|中亚)/.test(value)) {
    return { key: "central-asia", label: "中亚增强模式", description: "自动使用英语/俄语搜索，并优先识别当地官网、经销商目录和公开联系方式。" };
  }
  if (/(russia|russian federation|moscow|saint petersburg|st petersburg|россия|москва|俄罗斯)/.test(value)) {
    return { key: "russia", label: "俄罗斯增强模式", description: "自动使用俄语/英语搜索负责人、官网、经销商目录、Telegram 和公开联系方式。" };
  }
  if (/(singapore|malaysia|indonesia|thailand|vietnam|philippines|cambodia|laos|myanmar|brunei|southeast asia|asean|东南亚|新加坡|马来西亚|印度尼西亚|泰国|越南|菲律宾|柬埔寨|老挝|缅甸|文莱)/.test(value)) {
    return { key: "southeast-asia", label: "东南亚增强模式", description: "自动按国家使用英语和当地语言搜索，并补充经销商、官网、WhatsApp、Facebook 和公开联系方式。" };
  }
  if (/(india|pakistan|bangladesh|sri lanka|nepal|south asia|南亚)/.test(value)) {
    return { key: "south-asia", label: "南亚增强模式", description: "自动按国家搜索公司与负责人，并补充官网、社媒、电话和公开邮箱。" };
  }
  return { key: "global", label: "全球标准模式", description: "填写默认地区后，系统会自动切换对应的区域增强策略，无需手动选择。" };
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
