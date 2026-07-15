import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "./api.js";

const lifecycleOptions = [
  ["lead", "线索"],
  ["replied", "回复"],
  ["conversation", "初步沟通"],
  ["meeting", "约会/会议"],
  ["business_plan", "商业计划"],
  ["trial_order", "试订单"],
  ["agency_agreement", "代理协议"],
  ["store_creation", "门店创建"],
];

const activityTypes = [
  ["reply", "回复内容"],
  ["research", "客户资料/背景调研"],
  ["meeting_note", "会议纪要"],
  ["business_plan", "商业计划"],
  ["trial_order", "试订单"],
  ["agreement_review", "代理协议风险"],
  ["store_plan", "门店创建资料"],
  ["note", "普通备注"],
];

const lifecycleLabels = Object.fromEntries([
  ...lifecycleOptions,
  ["store_visit", "到店参观"],
  ["hq_visit", "总部拜访"],
  ["signed", "成功签约"],
  ["maintenance", "持续维护"],
  ["waiting_pool", "等待池"],
  ["abandoned", "已放弃"],
]);

export default function CustomerWorkspacePortal() {
  const [target, setTarget] = useState(null);

  useEffect(() => {
    const node = document.querySelector("#react-workspace-root");
    const workspace = document.querySelector("#customer-workspace");
    workspace?.classList.add("react-workspace-enabled");
    setTarget(node);
    return () => workspace?.classList.remove("react-workspace-enabled");
  }, []);

  if (!target) return null;
  return createPortal(<CustomerWorkspace />, target);
}

function CustomerWorkspace() {
  const [detail, setDetail] = useState(null);
  const [suggestions, setSuggestions] = useState([]);
  const [stage, setStage] = useState("lead");
  const [activityType, setActivityType] = useState("reply");
  const [content, setContent] = useState("");
  const [analysis, setAnalysis] = useState(null);
  const [emailMode, setEmailMode] = useState("ai");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [approved, setApproved] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const contact = detail?.contact;
  const activities = detail?.activities || [];

  const loadSuggestions = useCallback(async () => {
    const responses = await Promise.all([
      api("/api/contacts?limit=6&filter=draft_pending"),
      api("/api/contacts?limit=6&filter=draft_approved"),
      api("/api/contacts?limit=6&filter=missing_draft"),
    ]);
    const seen = new Set();
    const rows = responses.flatMap((response) => response.contacts || []).filter((item) => {
      if (seen.has(item.id)) return false;
      seen.add(item.id);
      return !["bounced", "unsubscribed"].includes(item.status);
    });
    setSuggestions(rows.slice(0, 9));
  }, []);

  const loadDetail = useCallback(async (contactId) => {
    if (!contactId) return;
    setLoading(true);
    setError("");
    try {
      const next = await api(`/api/contact-detail?contact_id=${encodeURIComponent(contactId)}`);
      if (!next.contact) throw new Error("客户不存在");
      setDetail(next);
      setStage(next.contact.lifecycle_stage || "lead");
      setActivityType("reply");
      setContent("");
      setAnalysis(null);
      setEmailMode("ai");
      setSubject(next.draft?.subject || defaultEmailSubject(next.contact));
      setBody(next.draft?.body || defaultEmailBody(next.contact));
      setEmailMode(next.draft?.mode || "ai");
      setApproved(next.draft?.status === "approved");
      setTimeout(() => document.querySelector("#customer-workspace")?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
    } catch (err) {
      setError(err.message);
      window.dispatchEvent(new CustomEvent("salesbot:notice", { detail: { message: err.message, type: "error" } }));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const open = (event) => loadDetail(Number(event.detail?.contactId));
    const refresh = () => loadSuggestions().catch(() => {});
    window.addEventListener("salesbot:open-contact", open);
    window.addEventListener("salesbot:refresh-related", refresh);
    loadSuggestions().catch(() => {});
    return () => {
      window.removeEventListener("salesbot:open-contact", open);
      window.removeEventListener("salesbot:refresh-related", refresh);
    };
  }, [loadDetail, loadSuggestions]);

  async function saveActivity() {
    if (!contact) throw new Error("请先选择客户");
    if (!content.trim()) throw new Error("请填写阶段记录");
    await api("/api/lifecycle-activity", {
      method: "POST",
      body: JSON.stringify({
        contact_id: contact.id,
        lifecycle_stage: stage,
        activity_type: activityType,
        content: content.trim(),
        created_by: "dashboard",
      }),
    });
    window.dispatchEvent(new CustomEvent("salesbot:notice", { detail: { message: "阶段记录已保存" } }));
    await loadDetail(contact.id);
    refreshRelatedViews();
  }

  async function analyzeStage(payload = {}) {
    if (!contact) throw new Error("请先选择客户");
    const result = await api("/api/stage-agent", {
      method: "POST",
      body: JSON.stringify({
        contact_id: contact.id,
        lifecycle_stage: stage,
        activity_type: activityType,
        content: content.trim(),
        ...payload,
      }),
    });
    setAnalysis(result.analysis);
    window.dispatchEvent(new CustomEvent("salesbot:notice", { detail: { message: "AI 阶段分析已生成" } }));
    if (payload.activity_id) await loadDetail(contact.id);
  }

  async function adoptEmail(email) {
    if (!contact) return;
    await api("/api/email-candidates/adopt", { method: "POST", body: JSON.stringify({ contact_id: contact.id, email }) });
    window.dispatchEvent(new CustomEvent("salesbot:notice", { detail: { message: `已采用候选邮箱：${email}` } }));
    await loadDetail(contact.id);
    refreshRelatedViews();
  }

  async function draftEmail() {
    if (!contact) throw new Error("请先选择客户");
    if (emailMode === "ai" && !detail?.research && contact.pool_type === "private") {
      const researched = await api("/api/contact-research", {
        method: "POST",
        body: JSON.stringify({ contact_id: contact.id }),
      });
      setDetail((current) => ({ ...current, research: researched.research }));
    }
    const result = await api("/api/email-draft", {
      method: "POST",
      body: JSON.stringify({ contact_id: contact.id, mode: emailMode, subject, body }),
    });
    setSubject(result.subject || "");
    setBody(result.body || "");
    setApproved(false);
    window.dispatchEvent(new CustomEvent("salesbot:notice", { detail: { message: "邮件草稿已生成，请检查后再发送" } }));
  }

  async function researchContact() {
    if (!contact) throw new Error("请先选择客户");
    if (contact.pool_type !== "private") throw new Error("请先领取客户，再执行外部调研");
    const result = await api("/api/contact-research", {
      method: "POST",
      body: JSON.stringify({ contact_id: contact.id, force: true }),
    });
    setDetail((current) => ({ ...current, research: result.research }));
    window.dispatchEvent(new CustomEvent("salesbot:notice", { detail: { message: `调研完成：找到 ${(result.research?.sources || []).length} 条公开证据` } }));
  }

  async function sendCustomEmail() {
    if (!contact) throw new Error("请先选择客户");
    if (!subject.trim() || !body.trim()) throw new Error("请先填写主题和正文");
    if (!approved) throw new Error("请先审核并锁定当前邮件草稿");
    if (!window.confirm("确认发送给当前客户？dry_run=false 时会真实发出。")) return;
    const result = await api("/api/send-custom", {
      method: "POST",
      body: JSON.stringify({ contact_id: contact.id, mode: emailMode, subject: subject.trim(), body: body.trim() }),
    });
    window.dispatchEvent(new CustomEvent("salesbot:notice", { detail: { message: `邮件已发送：第 ${result.step} 封` } }));
    if (result.usage) window.dispatchEvent(new CustomEvent("salesbot:usage", { detail: { usage: result.usage } }));
    await loadDetail(contact.id);
    refreshRelatedViews();
  }

  async function approveEmail() {
    if (!contact) throw new Error("请先选择客户");
    if (!subject.trim() || !body.trim()) throw new Error("请先填写主题和正文");
    const saved = await api("/api/email-draft", {
      method: "POST",
      body: JSON.stringify({ contact_id: contact.id, mode: "custom", subject: subject.trim(), body: body.trim() }),
    });
    setSubject(saved.subject || subject.trim());
    setBody(saved.body || body.trim());
    await api("/api/email-draft/approve", { method: "POST", body: JSON.stringify({ contact_id: contact.id }) });
    setApproved(true);
    window.dispatchEvent(new CustomEvent("salesbot:notice", { detail: { message: "邮件草稿已审核锁定，可以发送" } }));
  }

  async function guarded(action) {
    setError("");
    setLoading(true);
    try {
      await action();
    } catch (err) {
      setError(err.message);
      window.dispatchEvent(new CustomEvent("salesbot:notice", { detail: { message: err.message, type: "error" } }));
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <div className="followup-head">
        <div>
          <span className="eyebrow">Customer workspace</span>
          <h2>客户触达工作台</h2>
        </div>
        <p>记录回复、沟通、会议、商业计划、试订单、协议和门店信息，并让 AI 生成阶段建议。</p>
      </div>
      {!contact ? (
        <div className="workspace-empty workspace-picker">{loading ? "正在加载客户..." : <><strong>选择一个待触达客户</strong><span>队列按“待审核 → 已审核可发送 → 待生成草稿”排列。</span>{suggestions.length ? <div className="workspace-suggestions">{suggestions.map((item) => <button type="button" key={item.id} onClick={() => loadDetail(item.id)}><span><b>{[item.first_name, item.last_name].filter(Boolean).join(" ") || item.company_name}</b><small>{item.company_name || item.company_domain || ""}</small></span><em>{draftActionLabel(item)}</em></button>)}</div> : <a className="empty-state-action" href="#research">去领取或核验客户</a>}</>}</div>
      ) : (
        <div className="workspace-content">
          {error && <div className="admin-alert is-error">{error}</div>}
          <WorkflowStrip contact={contact} research={detail?.research} draft={detail?.draft} feedback={detail?.feedback} />
          <WorkspaceProfile contact={contact} research={detail?.research} onResearch={() => guarded(researchContact)} onAdoptEmail={(email) => guarded(() => adoptEmail(email))} />
          <div className="workspace-form">
            <label>阶段
              <select value={stage} onChange={(event) => setStage(event.target.value)}>
                {lifecycleOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
            <label>记录类型
              <select value={activityType} onChange={(event) => setActivityType(event.target.value)}>
                {activityTypes.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
            <label className="wide">阶段记录
              <textarea value={content} onChange={(event) => setContent(event.target.value)} placeholder="粘贴客户回复、会议纪要、订单信息、协议条款、门店资料等" />
            </label>
            <div className="panel-actions">
              <button type="button" disabled={loading} onClick={() => guarded(saveActivity)}>保存记录</button>
              <button type="button" disabled={loading} className="primary" onClick={() => guarded(() => analyzeStage())}>AI 分析阶段</button>
            </div>
          </div>
          <StageAnalysis analysis={analysis} />
          <div className="email-composer">
            <div className="composer-head">
              <div>
                <strong>邮件跟进</strong>
                <span>可以自定义邮件内容，或让 AI 根据客户线索和阶段记录生成个性化邮件。</span>
              </div>
              <label>模式
                <select value={emailMode} onChange={(event) => { setEmailMode(event.target.value); setApproved(false); }}>
                  <option value="ai">AI 个性化生成</option>
                  <option value="custom">自定义邮件</option>
                </select>
              </label>
            </div>
            <label>主题<input value={subject} onChange={(event) => { setSubject(event.target.value); setApproved(false); }} placeholder="邮件主题" /></label>
            <label>正文<textarea value={body} onChange={(event) => { setBody(event.target.value); setApproved(false); }} placeholder="邮件正文。可使用 {{first_name}}、{{company_name}}、{{unsubscribe_url}}" /></label>
            <div className={`draft-approval ${approved ? "approved" : "pending"}`}><strong>{approved ? "草稿已审核锁定" : "草稿尚未审核"}</strong><span>{approved ? "若修改主题或正文，需要重新审核。" : "检查收件人、事实依据、主题和正文后再锁定发送。"}</span></div>
            <div className="panel-actions">
              <button type="button" disabled={loading} onClick={() => guarded(draftEmail)}>{loading ? "处理中..." : "生成/套用草稿"}</button>
              <button type="button" disabled={loading} onClick={() => guarded(approveEmail)}>审核并锁定</button>
              <button type="button" disabled={loading || !approved} className="primary" onClick={() => guarded(sendCustomEmail)}>发送已审核邮件</button>
            </div>
          </div>
          <ActivityList activities={activities} onAnalyze={(activityId) => guarded(() => analyzeStage({ activity_id: activityId }))} />
        </div>
      )}
    </>
  );
}

function WorkflowStrip({ contact, research, draft, feedback }) {
  const identityReady = ["confirmed", "likely"].includes(contact.identity_status) || Number(contact.identity_confidence || 0) >= 70;
  const emailReady = contact.email_status === "valid" && !!contact.email;
  const researchReady = Array.isArray(research?.sources) && research.sources.length > 0;
  const draftReady = !!draft?.body;
  const sentReady = Number(contact.sequence_step || 0) > 0;
  const replied = contact.status === "replied" || Number(feedback?.replied || 0) > 0;
  const opened = Number(feedback?.opened || 0) > 0;
  const steps = [
    ["身份匹配", identityReady, identityReady ? `${contact.identity_confidence || contact.lead_score || "--"} 分` : "待确认"],
    ["邮箱验证", emailReady, emailReady ? "valid" : "待富化"],
    ["实时调研", researchReady, researchReady ? `${research.sources.length} 条证据` : "待调研"],
    ["邮件草稿", draftReady, draftReady ? "已保存" : "待生成"],
    ["发送触达", sentReady, sentReady ? `第 ${contact.sequence_step} 封` : "待发送"],
    ["行为回流", replied || opened, replied ? "已回复" : opened ? "已打开" : "等待反馈"],
  ];
  return <section className="workflow-strip">{steps.map(([label, done, note], index) => <article key={label} className={done ? "done" : "pending"}><b>{index + 1}</b><div><strong>{label}</strong><span>{note}</span></div></article>)}</section>;
}

function refreshRelatedViews() {
  window.dispatchEvent(new CustomEvent("salesbot:contacts-refresh"));
  window.dispatchEvent(new CustomEvent("salesbot:refresh-related"));
}

function draftActionLabel(contact) {
  if (contact.draft_status === "draft") return "待审核";
  if (contact.draft_status === "approved") return "可发送";
  return "待写草稿";
}

function WorkspaceProfile({ contact, research, onResearch, onAdoptEmail }) {
  const insights = contact.profile_insights || {};
  return (
    <div className="workspace-profile">
      <div><strong>{fullName(contact)}</strong><span>{contact.job_title || ""} · {contact.company_name || ""}</span></div>
      <div><b>{lifecycleLabels[contact.lifecycle_stage] || contact.lifecycle_stage || "线索"}</b><span>{dispositionLabel(contact.disposition)}</span></div>
      <div><b>{insights.icp_fit_score ?? "--"}</b><span>拟合度 / {intentLabel(insights.intent_level)}</span></div>
      <p>{contact.profile_summary || "还没有客户画像，点击列表里的“画像”生成。"}</p>
      <div className="panel-actions"><button type="button" onClick={onResearch} disabled={contact.pool_type !== "private"}>{research ? "刷新实时调研" : "调研公司与实时新闻"}</button></div>
      <ResearchEvidence research={research} />
      <EnhancedProfileBlocks insights={insights} />
      <PhoneCandidates contact={contact} />
      <EmailCandidates contact={contact} onAdoptEmail={onAdoptEmail} />
    </div>
  );
}

function ResearchEvidence({ research }) {
  if (!research) return <section className="research-evidence empty"><header><strong>公开调研证据</strong><span>生成 AI 邮件前会自动调研</span></header></section>;
  const sources = Array.isArray(research.sources) ? research.sources.slice(0, 6) : [];
  return (
    <section className="research-evidence">
      <header><strong>公开调研证据</strong><span>{research.provider || "search"} · {formatDate(research.researched_at)}</span></header>
      <p>{research.summary || ""}</p>
      <div className="research-source-list">
        {sources.map((source, index) => <a key={`${source.url}-${index}`} href={source.url} target="_blank" rel="noreferrer"><b>{source.type === "news" ? "新闻" : source.type === "person" ? "个人" : "公司"}</b><span>{source.title}</span><small>{source.published_at || source.domain || "日期未知"}</small></a>)}
        {!sources.length && <span className="muted">没有找到可引用的公开证据，邮件不会虚构新闻。</span>}
      </div>
    </section>
  );
}

function EnhancedProfileBlocks({ insights }) {
  return (
    <>
      <PainPointStrategy insights={insights} />
      <FollowupPlan insights={insights} />
    </>
  );
}

function PainPointStrategy({ insights }) {
  const strategy = insights?.pain_point_strategy || {};
  const rows = [
    ["Suspected pain", strategy.suspected_pain],
    ["Outreach angle", strategy.outreach_angle],
    ["Message hook", strategy.message_hook],
    ["Evidence", strategy.evidence_to_use],
    ["Question", strategy.question_to_ask],
    ["Avoid", strategy.avoid],
  ].filter(([, value]) => value);
  if (!rows.length) return null;
  return (
    <section className="pain-strategy">
      <header><strong>AI pain-point strategy</strong><span>Use as hypothesis, not invented fact</span></header>
      {rows.map(([label, value]) => (
        <div className="strategy-row" key={label}><b>{label}</b><span>{value}</span></div>
      ))}
    </section>
  );
}

function FollowupPlan({ insights }) {
  const plan = Array.isArray(insights?.followup_plan) ? insights.followup_plan : [];
  if (!plan.length) return null;
  return (
    <section className="followup-plan-box">
      <header><strong>14-day follow-up plan</strong><span>Day 1 / 3 / 7 / 14</span></header>
      <div className="followup-plan-grid">
        {plan.map((item, index) => (
          <article key={`${item.day || index}-${item.trigger || ""}`}>
            <b>{item.day || `Step ${index + 1}`}</b>
            <span>{item.trigger || ""}</span>
            <strong>{item.goal || ""}</strong>
            <p>{item.message || ""}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

function PhoneCandidates({ contact }) {
  const candidates = Array.isArray(contact.phone_candidates) ? contact.phone_candidates.slice(0, 5) : [];
  if (!contact.phone && !candidates.length) return null;
  return (
    <section className="email-candidates">
      <header><strong>电话候选</strong><span>电话通常来自导入表或社媒数据源，建议人工确认后使用</span></header>
      {contact.phone && <div className="candidate-row"><strong>{contact.phone}</strong><span>主电话</span><span>provided</span><span>known</span><b>--</b></div>}
      {candidates.map((item, index) => (
        <div key={`${item.phone}-${index}`} className="candidate-row">
          <strong>{item.phone || ""}</strong>
          <span>{item.source || ""}</span>
          <span>{item.type || "phone"}</span>
          <span>{item.status || "candidate"}</span>
          <b>{item.confidence ? `${item.confidence}%` : "--"}</b>
        </div>
      ))}
    </section>
  );
}
function EmailCandidates({ contact, onAdoptEmail }) {
  const candidates = Array.isArray(contact.email_candidates) ? contact.email_candidates.slice(0, 6) : [];
  if (!candidates.length) {
    return <section className="email-candidates empty"><header><strong>邮箱候选</strong><span>暂无候选</span></header></section>;
  }
  return (
    <section className="email-candidates">
      <header><strong>邮箱候选</strong><span>只把个人 valid 邮箱作为正式发信邮箱</span></header>
      {candidates.map((item) => (
        <div key={`${item.email}-${item.source}`} className={`candidate-row ${item.category || ""}`}>
          <strong>{item.email || ""}</strong>
          <span>{item.source || ""}</span>
          <span>{candidateCategoryLabel(item.category)}</span>
          <span>{item.status || "unknown"}</span>
          <b>{Number(item.confidence || 0)}%</b>
          {item.category === "personal_work" && <button type="button" onClick={() => onAdoptEmail(item.email)}>采用</button>}
        </div>
      ))}
    </section>
  );
}

function ActivityList({ activities, onAnalyze }) {
  if (!activities.length) return <div className="empty-activity">还没有阶段记录。</div>;
  return (
    <div className="activity-list">
      {activities.map((item) => (
        <article className="activity-card" key={item.id}>
          <header><strong>{lifecycleLabels[item.lifecycle_stage] || item.lifecycle_stage} / {activityTypeLabel(item.activity_type)}</strong><span>{formatDate(item.created_at)}</span></header>
          <p>{item.content}</p>
          <StageAnalysis analysis={item.ai_analysis} />
          <button type="button" onClick={() => onAnalyze(item.id)}>重新分析</button>
        </article>
      ))}
    </div>
  );
}

function StageAnalysis({ analysis }) {
  if (!analysis) return null;
  const data = typeof analysis === "string" ? { summary: analysis } : analysis;
  return (
    <div className="stage-analysis active">
      <strong>{data.summary || "AI 分析"}</strong>
      {data.intent && <p>意向判断：{data.intent}</p>}
      {data.next_step && <p>下一步：{data.next_step}</p>}
      {Array.isArray(data.risks) && data.risks.length > 0 && <p>风险：{data.risks.join("；")}</p>}
      {Array.isArray(data.prep_materials) && data.prep_materials.length > 0 && <p>准备材料：{data.prep_materials.join("；")}</p>}
    </div>
  );
}

function fullName(contact) {
  return [contact.first_name, contact.last_name].filter(Boolean).join(" ") || "(No name)";
}

function dispositionLabel(disposition) {
  return { active: "推进中", waiting: "等待", abandoned: "已放弃", won: "已签约", lost: "流失" }[disposition] || disposition || "推进中";
}

function intentLabel(level) {
  return { high: "高意向", medium: "中意向", low: "低意向", unknown: "待判断" }[level] || "待判断";
}

function candidateCategoryLabel(category) {
  return { personal_work: "个人工作邮箱", personal_free: "个人邮箱", company_generic: "公司通用邮箱" }[category] || "未分类";
}

function activityTypeLabel(type) {
  return Object.fromEntries(activityTypes)[type] || type;
}

function formatDate(value) {
  if (!value) return "";
  return String(value).replace("T", " ").slice(0, 16);
}

function defaultEmailSubject(contact) {
  if (isInternalTestContact(contact)) return "[Test] Outbound Ops delivery and feedback flow";
  const company = contact?.company_name || "your business";
  return `Possible Vertu channel fit for ${company}`;
}

function defaultEmailBody(contact) {
  const firstName = contact?.first_name || "there";
  if (isInternalTestContact(contact)) {
    return [
      `Hi ${firstName},`,
      "",
      "This is a controlled end-to-end test from Outbound Ops. It is checking email delivery, open tracking, reply routing, and lifecycle updates.",
      "",
      "Please open this email and reply with: 回流测试收到",
      "",
      "Best regards,",
      "{{sender_name}} You",
      "BD Manager Of Media East Region | VERTU",
      "",
      "Unsubscribe: {{unsubscribe_url}}",
    ].join("\n");
  }
  const company = contact?.company_name || "your company";
  const role = contact?.job_title || "your team";
  const context = contact?.source_context || {};
  const reason = context.seed_reason || context.reason || "";
  const category = context.seed_category || contact?.industry || "premium retail/distribution";
  const matchLine = reason
    ? `I noticed ${company} in our market research: ${reason}`
    : `I noticed ${company} is relevant to ${category}, and your role as ${role} looks close to channel or commercial decisions.`;
  return [
    `Hi ${firstName},`,
    "",
    matchLine,
    "",
    "I work with Vertu, a premium mobile and luxury technology brand. We are looking for selective partners where the customer base already values high-end products, service, and differentiated retail experiences.",
    "",
    `If ${company} is exploring new premium categories or partner brands, I can send a short note on where Vertu may fit and what a lightweight cooperation model could look like.`,
    "",
    "Would it be worth a brief reply to see if this is relevant?",
    "",
    "Best regards,",
    "{{sender_name}} You",
    "BD Manager Of Media East Region | VERTU",
    "",
    "Unsubscribe: {{unsubscribe_url}}",
  ].join("\n");
}

function isInternalTestContact(contact) {
  return /@vertu\.(?:cn|com)$/i.test(contact?.email || "");
}
