import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "./api.js";
import { rememberWorkspaceContact, selectedWorkspaceContact } from "./workspaceNavigation.js";

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
  const [qualityReview, setQualityReview] = useState(null);
  const [approved, setApproved] = useState(false);
  const [loading, setLoading] = useState(false);
  const [operationLabel, setOperationLabel] = useState("");
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
      setQualityReview(next.draft?.quality_review || null);
      setTimeout(() => document.querySelector("#customer-workspace")?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
    } catch (err) {
      setError(err.message);
      window.dispatchEvent(new CustomEvent("salesbot:notice", { detail: { message: err.message, type: "error" } }));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const open = (event) => {
      const contactId = rememberWorkspaceContact(event.detail?.contactId);
      if (contactId) loadDetail(contactId);
    };
    const refresh = () => loadSuggestions().catch(() => {});
    window.addEventListener("salesbot:open-contact", open);
    window.addEventListener("salesbot:refresh-related", refresh);
    loadSuggestions().catch(() => {});
    const selectedContactId = selectedWorkspaceContact();
    if (selectedContactId) loadDetail(selectedContactId);
    return () => {
      window.removeEventListener("salesbot:open-contact", open);
      window.removeEventListener("salesbot:refresh-related", refresh);
    };
  }, [loadDetail, loadSuggestions]);

  useEffect(() => {
    if (!contact?.id) return undefined;
    const refresh = async () => {
      try {
        const next = await api(`/api/contact-detail?contact_id=${encodeURIComponent(contact.id)}`);
        if (next.contact) setDetail(next);
      } catch {
        // Keep the current form usable during a transient refresh failure.
      }
    };
    const interval = window.setInterval(refresh, 30_000);
    const handleVisibility = () => {
      if (document.visibilityState === "visible") refresh();
    };
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [contact?.id]);

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
    try {
      if (emailMode === "ai" && !detail?.research && contact.pool_type === "private") {
        setOperationLabel("正在调研公司与实时新闻...");
        const researched = await api("/api/contact-research", {
          method: "POST",
          body: JSON.stringify({ contact_id: contact.id }),
        });
        setDetail((current) => ({ ...current, research: researched.research }));
      }
      setOperationLabel(emailMode === "ai" ? "正在生成个性化草稿..." : "正在套用自定义草稿...");
      const result = await api("/api/email-draft", {
        method: "POST",
        body: JSON.stringify({ contact_id: contact.id, mode: emailMode, subject, body }),
      });
      setSubject(result.subject || "");
      setBody(result.body || "");
      setQualityReview(result.quality_review || null);
      setApproved(false);
      window.dispatchEvent(new CustomEvent("salesbot:notice", { detail: { message: "邮件草稿已生成，请检查后再发送" } }));
    } finally {
      setOperationLabel("");
    }
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
    const recipient = contact.email || "当前客户";
    const name = [contact.first_name, contact.last_name].filter(Boolean).join(" ") || contact.company_name || "当前客户";
    if (!window.confirm(`确认真实发送给 ${name}（${recipient}）？发送后会计入今日发信额度。`)) return;
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
    setQualityReview(saved.quality_review || null);
    await api("/api/email-draft/approve", { method: "POST", body: JSON.stringify({ contact_id: contact.id }) });
    setApproved(true);
    window.dispatchEvent(new CustomEvent("salesbot:notice", { detail: { message: "邮件草稿已审核锁定，可以发送" } }));
  }

  async function reviewIcp(expectedQualified) {
    if (!contact) return;
    await api("/api/icp-feedback", {
      method: "POST",
      body: JSON.stringify({ contact_id: contact.id, expected_qualified: expectedQualified }),
    });
    window.dispatchEvent(new CustomEvent("salesbot:notice", { detail: { message: "ICP 判断反馈已记录" } }));
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
          <WorkspaceProfile
            contact={contact}
            research={detail?.research}
            onResearch={() => guarded(researchContact)}
            onAdoptEmail={(email) => guarded(() => adoptEmail(email))}
            onIcpFeedback={(expected) => guarded(() => reviewIcp(expected))}
          />
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
            <ComposerSteps draft={detail?.draft} approved={approved} sent={Number(contact.sequence_step || 0) > 0} />
            <label>主题<input value={subject} onChange={(event) => { setSubject(event.target.value); setApproved(false); setQualityReview(null); }} placeholder="邮件主题" /></label>
            <label>正文<textarea value={body} onChange={(event) => { setBody(event.target.value); setApproved(false); setQualityReview(null); }} placeholder="邮件正文。可使用 {{first_name}}、{{company_name}}、{{unsubscribe_url}}" /></label>
            <CopyQualityReview review={qualityReview} />
            <div className={`draft-approval ${approved ? "approved" : "pending"}`}><strong>{approved ? "草稿已审核锁定" : "草稿尚未审核"}</strong><span>{approved ? "若修改主题或正文，需要重新审核。" : "检查收件人、事实依据、主题和正文后再锁定发送。"}</span></div>
            <div className="panel-actions">
              <button type="button" disabled={loading} onClick={() => guarded(draftEmail)}>{loading ? (operationLabel || "处理中...") : (emailMode === "ai" ? "调研并生成草稿" : "套用自定义草稿")}</button>
              <button type="button" disabled={loading} onClick={() => guarded(approveEmail)}>审核并锁定</button>
              <button type="button" disabled={loading || !approved} className="primary" onClick={() => guarded(sendCustomEmail)}>发送已审核邮件</button>
            </div>
            {!approved && <p className="composer-send-note">发送按钮会在草稿审核锁定后启用，避免误发未确认内容。</p>}
            {loading && operationLabel && <div className="composer-progress" role="status" aria-live="polite"><span className="spinner" aria-hidden="true" /><div><strong>{operationLabel}</strong><small>实时调研和 AI 生成通常需要 10-30 秒，请勿重复点击。</small></div></div>}
          </div>
          <ActivityList activities={activities} onAnalyze={(activityId) => guarded(() => analyzeStage({ activity_id: activityId }))} />
        </div>
      )}
    </>
  );
}

function ComposerSteps({ draft, approved, sent }) {
  const drafted = Boolean(draft?.body);
  const steps = [
    ["生成草稿", drafted],
    ["审核锁定", approved],
    ["发送邮件", sent],
  ];
  const current = sent ? -1 : approved ? 2 : drafted ? 1 : 0;
  return (
    <ol className="composer-steps" aria-label="邮件发送进度">
      {steps.map(([label, done], index) => (
        <li key={label} className={done ? "done" : index === current ? "current" : "pending"} aria-current={index === current ? "step" : undefined}>
          <b>{done ? "✓" : index + 1}</b><span>{label}</span>
        </li>
      ))}
    </ol>
  );
}

function WorkflowStrip({ contact, research, draft, feedback }) {
  const icp = contact.icp_assessment || {};
  const identityReady = icp.qualified || ["confirmed", "likely"].includes(contact.identity_status) || Number(contact.identity_confidence || 0) >= 70;
  const emailReady = contact.email_status === "valid" && !!contact.email;
  const researchReady = Array.isArray(research?.sources) && research.sources.length > 0;
  const draftReady = !!draft?.body;
  const sentReady = Number(contact.sequence_step || 0) > 0;
  const replied = contact.status === "replied" || Number(feedback?.replied || 0) > 0;
  const opened = Number(feedback?.opened || 0) > 0;
  const steps = [
    ["ICP 与身份", identityReady, icp.score != null ? `${icp.score} 分 · ${icpTierLabel(icp.tier)}` : identityReady ? `${contact.identity_confidence || contact.lead_score || "--"} 分` : "待确认"],
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

function WorkspaceProfile({ contact, research, onResearch, onAdoptEmail, onIcpFeedback }) {
  const insights = contact.profile_insights || {};
  const icp = contact.icp_assessment || insights.icp_assessment || {};
  return (
    <div className="workspace-profile">
      <div><strong>{fullName(contact)}</strong><span>{contact.job_title || ""} · {contact.company_name || ""}</span></div>
      <div><b>{lifecycleLabels[contact.lifecycle_stage] || contact.lifecycle_stage || "线索"}</b><span>{dispositionLabel(contact.disposition)}</span></div>
      <div><b>{icp.score ?? insights.icp_fit_score ?? "--"}</b><span>ICP · {icpTierLabel(icp.tier)} / {intentLabel(insights.intent_level)}</span></div>
      <p>{contact.profile_summary || "还没有客户画像，点击列表里的“画像”生成。"}</p>
      {icp.score != null && <section className={`icp-assessment tier-${icp.tier || "review"}`}>
        <div>
          <strong>{icp.qualified ? "符合当前 ICP" : "需要人工复核"}</strong>
          <span>置信度 {icp.confidence ?? "--"} · {Object.entries(icp.breakdown || {}).map(([key, value]) => `${icpDimensionLabel(key)} ${value}`).join(" / ")}</span>
        </div>
        <div className="icp-feedback-actions">
          <span>判断准确吗？</span>
          <button type="button" onClick={() => onIcpFeedback(true)}>适合跟进</button>
          <button type="button" onClick={() => onIcpFeedback(false)}>不匹配</button>
        </div>
      </section>}
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
          {item.category === "personal_work" && item.status === "valid"
            ? <button type="button" onClick={() => onAdoptEmail(item.email)}>采用</button>
            : <small>{item.category === "personal_work" ? "需进一步验证" : "仅供参考"}</small>}
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

function CopyQualityReview({ review }) {
  if (!review?.status) {
    return (
      <section className="copy-quality neutral">
        <div>
          <strong>发送前自动质检</strong>
          <span>重新生成或套用当前草稿后，系统会检查垃圾词、事实风险、格式和行动指令。</span>
        </div>
      </section>
    );
  }
  const issues = [...(review.blocking_issues || []), ...(review.warnings || [])];
  const statusLabel = {
    ready: "可发送",
    revise: "建议修改",
    blocked: "已拦截",
  }[review.status] || "待检查";
  return (
    <section className={`copy-quality ${review.status || "neutral"}`}>
      <div className="copy-quality-head">
        <div>
          <strong>{statusLabel}</strong>
          <span>文案质量 {Number(review.score || 0)} 分</span>
        </div>
        <b>{review.status === "blocked" ? "修正后才能审核" : review.status === "revise" ? "建议先优化" : "通过自动检查"}</b>
      </div>
      {review.rules && (
        <div className="copy-quality-rules" aria-label="冷邮件写作规则">
          <span className={review.rules.peer_to_peer ? "pass" : "fail"}>同行语气</span>
          <span className={review.rules.word_count_in_range ? "pass" : "fail"}>
            正文 {Number(review.rules.prospect_word_count || 0)} 词（目标 70-100）
          </span>
          <span className={review.rules.single_low_friction_cta ? "pass" : "fail"}>单一低门槛 CTA</span>
        </div>
      )}
      {issues.length > 0 && (
        <ul className="quality-issue-list">
          {issues.slice(0, 5).map((issue, index) => (
            <li key={`${issue.code || issue}-${index}`}>{copyIssueLabel(issue)}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

function copyIssueLabel(issue) {
  const code = typeof issue === "string" ? issue : issue?.code;
  const detail = typeof issue === "object" ? issue?.message || issue?.detail || issue?.recommendation : "";
  const labels = {
    missing_subject: "缺少具体主题",
    body_too_short: "正文太短，缺少价值信息和明确问题",
    body_too_long: "正文过长，建议压缩",
    body_below_target_words: "正文少于 70 词，补足一条真实观察或本地渠道价值",
    body_above_target_words: "正文超过 100 词，删除重复介绍和泛化宣传（产品图已展示品类）",
    unresolved_placeholders: "存在未处理的模板变量",
    internal_data_exposed: "正文暴露了内部评分、核验或来源字段",
    fake_urgency: "存在人为制造紧迫感的措辞",
    unverifiable_return: "包含无法核实的收益承诺",
    generic_flattery: "开场赞美过于泛化",
    template_cliche: "包含明显模板化开场",
    salesy_pitch: "语气像推销广告，改成同行之间的商业判断",
    excessive_exclamation: "感叹号过多",
    subject_all_caps: "主题包含连续大写词",
    missing_question: "缺少低门槛的确认问题",
    too_many_questions: "问题过多，只保留一个主要行动指令",
    high_friction_cta: "首封不要直接约会；改为询问是否可发送一页合作思路",
    missing_unsubscribe: "缺少退订链接",
    missing_greeting: "缺少自然称呼",
    missing_cta: "缺少清晰且低门槛的下一步",
    too_long: "正文过长，建议压缩",
    too_many_links: "链接过多，可能影响送达",
    excessive_caps: "大写内容过多，像群发广告",
    spam_phrase: "包含高风险营销措辞",
    unsupported_claim: "存在未经证据支持的事实或收益承诺",
    malformed_placeholder: "模板变量格式不正确",
    weak_personalization: "个性化信息不足",
  };
  return detail || labels[code] || String(code || "需要人工复核");
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

function icpTierLabel(tier) {
  return {
    priority: "高优先",
    qualified: "符合",
    review: "需复核",
    disqualified: "不建议",
  }[tier] || "待评估";
}

function icpDimensionLabel(dimension) {
  return {
    contactability: "联系方式",
    role_authority: "职位",
    account_fit: "公司",
    identity_quality: "身份",
    market_evidence: "市场证据",
  }[dimension] || dimension;
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
    "From VERTU headquarters, I work with prospective local partners on whether a VERTU boutique or selective distribution model could suit their market.",
    "",
    "VERTU combines luxury mobile products, accessories and a differentiated retail experience for high-value customers. For the right operator, this can create a distinct premium category alongside an existing luxury portfolio, subject to a practical local market plan.",
    "",
    `May I send a one-page view of how a VERTU channel partnership could be assessed for ${company}'s market?`,
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
