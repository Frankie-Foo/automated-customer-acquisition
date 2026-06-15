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
  const [stage, setStage] = useState("lead");
  const [activityType, setActivityType] = useState("reply");
  const [content, setContent] = useState("");
  const [analysis, setAnalysis] = useState(null);
  const [emailMode, setEmailMode] = useState("ai");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const contact = detail?.contact;
  const activities = detail?.activities || [];

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
      setSubject(`Quick question about ${next.contact.company_name || "your business"}`);
      setBody("");
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
    window.addEventListener("salesbot:open-contact", open);
    return () => window.removeEventListener("salesbot:open-contact", open);
  }, [loadDetail]);

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
    window.dispatchEvent(new CustomEvent("salesbot:contacts-refresh"));
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
    window.dispatchEvent(new CustomEvent("salesbot:contacts-refresh"));
  }

  async function draftEmail() {
    if (!contact) throw new Error("请先选择客户");
    const result = await api("/api/email-draft", {
      method: "POST",
      body: JSON.stringify({ contact_id: contact.id, mode: emailMode, subject, body }),
    });
    setSubject(result.subject || "");
    setBody(result.body || "");
    window.dispatchEvent(new CustomEvent("salesbot:notice", { detail: { message: "邮件草稿已生成，请检查后再发送" } }));
  }

  async function sendCustomEmail() {
    if (!contact) throw new Error("请先选择客户");
    if (!subject.trim() || !body.trim()) throw new Error("请先填写主题和正文");
    if (!window.confirm("确认发送给当前客户？dry_run=false 时会真实发出。")) return;
    const result = await api("/api/send-custom", {
      method: "POST",
      body: JSON.stringify({ contact_id: contact.id, mode: emailMode, subject: subject.trim(), body: body.trim() }),
    });
    window.dispatchEvent(new CustomEvent("salesbot:notice", { detail: { message: `邮件已发送：第 ${result.step} 封` } }));
    if (result.usage) window.dispatchEvent(new CustomEvent("salesbot:usage", { detail: { usage: result.usage } }));
    await loadDetail(contact.id);
    window.dispatchEvent(new CustomEvent("salesbot:contacts-refresh"));
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
          <h2>客户详情与阶段工作台</h2>
        </div>
        <p>记录回复、沟通、会议、商业计划、试订单、协议和门店信息，并让 AI 生成阶段建议。</p>
      </div>
      {!contact ? (
        <div className="workspace-empty">{loading ? "正在加载客户..." : "从客户列表点击“详情”开始管理客户生命周期。"}</div>
      ) : (
        <div className="workspace-content">
          {error && <div className="admin-alert is-error">{error}</div>}
          <WorkspaceProfile contact={contact} onAdoptEmail={(email) => guarded(() => adoptEmail(email))} />
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
              <button type="button" onClick={() => guarded(saveActivity)}>保存记录</button>
              <button type="button" className="primary" onClick={() => guarded(() => analyzeStage())}>AI 分析阶段</button>
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
                <select value={emailMode} onChange={(event) => setEmailMode(event.target.value)}>
                  <option value="ai">AI 个性化生成</option>
                  <option value="custom">自定义邮件</option>
                </select>
              </label>
            </div>
            <label>主题<input value={subject} onChange={(event) => setSubject(event.target.value)} placeholder="邮件主题" /></label>
            <label>正文<textarea value={body} onChange={(event) => setBody(event.target.value)} placeholder="邮件正文。可使用 {{first_name}}、{{company_name}}、{{unsubscribe_url}}" /></label>
            <div className="panel-actions">
              <button type="button" onClick={() => guarded(draftEmail)}>生成/套用草稿</button>
              <button type="button" className="primary" onClick={() => guarded(sendCustomEmail)}>发送给当前客户</button>
            </div>
          </div>
          <ActivityList activities={activities} onAnalyze={(activityId) => guarded(() => analyzeStage({ activity_id: activityId }))} />
        </div>
      )}
    </>
  );
}

function WorkspaceProfile({ contact, onAdoptEmail }) {
  const insights = contact.profile_insights || {};
  return (
    <div className="workspace-profile">
      <div><strong>{fullName(contact)}</strong><span>{contact.job_title || ""} · {contact.company_name || ""}</span></div>
      <div><b>{lifecycleLabels[contact.lifecycle_stage] || contact.lifecycle_stage || "线索"}</b><span>{dispositionLabel(contact.disposition)}</span></div>
      <div><b>{insights.icp_fit_score ?? "--"}</b><span>拟合度 / {intentLabel(insights.intent_level)}</span></div>
      <p>{contact.profile_summary || "还没有客户画像，点击列表里的“画像”生成。"}</p>
      <EmailCandidates contact={contact} onAdoptEmail={onAdoptEmail} />
    </div>
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
