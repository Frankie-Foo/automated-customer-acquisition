import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "./api.js";

export default function SentEmailsPortal() {
  const [target, setTarget] = useState(null);

  useEffect(() => {
    setTarget(document.querySelector("#react-sent-emails-root"));
  }, []);

  if (!target) return null;
  return createPortal(<SentEmails />, target);
}

function SentEmails() {
  const [emails, setEmails] = useState([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const query = new URLSearchParams({ limit: "150" });
      if (search.trim()) query.set("search", search.trim());
      const data = await api(`/api/sent-emails?${query.toString()}`);
      setEmails(data.emails || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [search]);

  useEffect(() => {
    load().catch(() => {});
    window.addEventListener("salesbot:contacts-refresh", load);
    window.addEventListener("salesbot:refresh-related", load);
    return () => {
      window.removeEventListener("salesbot:contacts-refresh", load);
      window.removeEventListener("salesbot:refresh-related", load);
    };
  }, [load]);

  return (
    <>
      <div className="section-head">
        <div>
          <span className="eyebrow">Email log</span>
          <h2>已发送邮件</h2>
          <p>查看每封邮件从哪个邮箱发出、发给谁、标题是什么，以及后续送达、打开、回复和退信反馈。</p>
        </div>
        <div className="toolbar sent-toolbar">
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索收件人、公司、标题、发件邮箱" />
          <button type="button" onClick={load}>刷新</button>
        </div>
      </div>
      {error && <div className="admin-alert is-error">{error}</div>}
      <div className="email-log-guide">
        <span><b>送达</b> 表示 Resend 已投递到对方邮箱服务商。</span>
        <span><b>打开</b> 表示追踪像素被加载，不等于客户已回复。</span>
        <span><b>回复</b> 会回到“回复至”邮箱，同时需要 Webhook 回流才能自动计数。</span>
        <span><b>退信/退订</b> 客户不会继续显示发送按钮。</span>
      </div>
      <div className="table-shell">
        <table className="sent-email-table">
          <thead>
            <tr>
              <th>发送时间</th>
              <th>发件邮箱</th>
              <th>收件人</th>
              <th>标题</th>
              <th>客户</th>
              <th>Step</th>
              <th>反馈</th>
              <th>最后回流</th>
              <th>Message ID</th>
            </tr>
          </thead>
          <tbody>
            {loading && !emails.length ? (
              <tr><td colSpan="9"><div className="empty-state">正在加载已发送邮件...</div></td></tr>
            ) : emails.length ? (
              emails.map((email) => <SentEmailRow key={email.id} email={email} />)
            ) : (
              <tr><td colSpan="9"><div className="empty-state"><strong>暂无已发送邮件</strong><div>发送邮件后会自动出现在这里。</div></div></td></tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}

function SentEmailRow({ email }) {
  return (
    <tr>
      <td>{formatDate(email.occurred_at)}</td>
      <td><strong>{email.sender_email || "未记录"}</strong><div className="muted">回复至：{email.reply_to_email || "未记录"}</div>{email.dry_run && <div className="muted">dry run</div>}</td>
      <td><strong>{email.recipient_email || ""}</strong><div className="muted">{fullName(email)}</div></td>
      <td className="subject-cell">{email.email_subject || ""}</td>
      <td><strong>{email.company_name || ""}</strong><div className="muted">{email.company_domain || email.job_title || ""}</div></td>
      <td>{email.sequence_step || 0}</td>
      <td><FeedbackBadges email={email} /></td>
      <td>{formatDate(email.last_feedback_at)}<div className="muted">{feedbackLabel(email.last_feedback_type)}</div></td>
      <td className="message-id" title={email.message_id || ""}>{email.message_id || ""}</td>
    </tr>
  );
}

function FeedbackBadges({ email }) {
  const items = [];
  if (Number(email.delivered_count || 0) > 0) items.push(["delivered", `送达 ${email.delivered_count}`]);
  if (Number(email.opened_count || 0) > 0) items.push(["opened", `打开 ${email.opened_count}`]);
  if (Number(email.replied_count || 0) > 0) items.push(["replied", `回复 ${email.replied_count}`]);
  if (Number(email.bounced_count || 0) > 0) items.push(["bounced", `退信 ${email.bounced_count}`]);
  if (Number(email.complained_count || 0) > 0) items.push(["complained", `投诉 ${email.complained_count}`]);
  if (!items.length) items.push(["sent", "已发送"]);
  return <div className="event-list">{items.map(([type, label]) => <span key={type} className={`event-chip ${type}`}>{label}</span>)}</div>;
}

function fullName(row) {
  return [row.first_name, row.last_name].filter(Boolean).join(" ");
}

function feedbackLabel(type) {
  return { delivered: "送达", opened: "打开", clicked: "点击", replied: "回复", bounced: "退信", complained: "投诉", unsubscribed: "退订" }[type] || "";
}

function formatDate(value) {
  if (!value) return "";
  return String(value).replace("T", " ").slice(0, 16);
}
