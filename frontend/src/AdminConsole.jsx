import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "./api.js";

const emptyNewUser = {
  username: "",
  display_name: "",
  password: "",
  reply_to_email: "",
  daily_source_limit: 100,
  daily_send_limit: 200,
};

export default function AdminConsolePortal() {
  const [target, setTarget] = useState(null);

  useEffect(() => {
    setTarget(document.querySelector("#admin-console"));
  }, []);

  if (!target) return null;
  return createPortal(<AdminConsole />, target);
}

function AdminConsole() {
  const [user, setUser] = useState(null);
  const [users, setUsers] = useState([]);
  const [senders, setSenders] = useState([]);
  const [auditLogs, setAuditLogs] = useState([]);
  const [newUser, setNewUser] = useState(emptyNewUser);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const isAdmin = user?.role === "admin";

  const loadAdminData = useCallback(async () => {
    if (!isAdmin) return;
    setLoading(true);
    setError("");
    try {
      const [userData, senderData, auditData] = await Promise.all([
        api("/api/admin/users"),
        api("/api/admin/senders"),
        api("/api/admin/audit-logs?limit=100"),
      ]);
      setUsers(userData.users || []);
      setSenders(senderData.senders || []);
      setAuditLogs(auditData.logs || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [isAdmin]);

  useEffect(() => {
    api("/api/me")
      .then((session) => setUser(session.user))
      .catch(() => setUser(null));
  }, []);

  useEffect(() => {
    const handleSession = (event) => {
      setUser(event.detail?.user || null);
    };
    const handleRefresh = () => loadAdminData();
    window.addEventListener("salesbot:session", handleSession);
    window.addEventListener("salesbot:admin-refresh", handleRefresh);
    return () => {
      window.removeEventListener("salesbot:session", handleSession);
      window.removeEventListener("salesbot:admin-refresh", handleRefresh);
    };
  }, [loadAdminData]);

  useEffect(() => {
    loadAdminData();
  }, [loadAdminData]);

  const summary = useMemo(() => {
    return {
      activeUsers: users.filter((item) => item.active).length,
      mustChange: users.filter((item) => item.must_change_password).length,
      sourceUsed: users.reduce((sum, item) => sum + Number(item.source_count_today || 0), 0),
      sendUsed: users.reduce((sum, item) => sum + Number(item.send_count_today || 0), 0),
      activeSenders: senders.filter((item) => item.active).length,
    };
  }, [users, senders]);

  async function createUser() {
    setError("");
    setMessage("");
    if (!newUser.username.trim() || !newUser.password) {
      setError("账号和密码必填");
      return;
    }
    try {
      await api("/api/admin/users", {
        method: "POST",
        body: JSON.stringify({
          ...newUser,
          username: newUser.username.trim(),
          display_name: newUser.display_name.trim(),
          role: "sales",
          daily_source_limit: Number(newUser.daily_source_limit || 100),
          daily_send_limit: Number(newUser.daily_send_limit || 100),
        }),
      });
      setMessage("销售账号已创建");
      setNewUser(emptyNewUser);
      await loadAdminData();
      window.dispatchEvent(new CustomEvent("salesbot:ops-refresh"));
    } catch (err) {
      setError(err.message);
    }
  }

  async function updateUser(userId, patch) {
    setError("");
    setMessage("");
    try {
      await api("/api/admin/user", { method: "POST", body: JSON.stringify({ user_id: userId, ...patch }) });
      setMessage("用户已更新");
      await loadAdminData();
      window.dispatchEvent(new CustomEvent("salesbot:ops-refresh"));
    } catch (err) {
      setError(err.message);
    }
  }

  async function resetPassword(userId) {
    const password = window.prompt("输入新密码，至少 8 位");
    if (!password) return;
    if (password.length < 8) {
      setError("密码至少 8 位");
      return;
    }
    await updateUser(userId, { password });
  }

  async function updateSender(senderId, patch) {
    setError("");
    setMessage("");
    try {
      await api("/api/admin/sender", { method: "POST", body: JSON.stringify({ sender_id: senderId, ...patch }) });
      setMessage("发件账号已更新");
      await loadAdminData();
    } catch (err) {
      setError(err.message);
    }
  }

  if (!isAdmin) {
    return null;
  }

  return (
    <>
      <div className="followup-head">
        <div>
          <span className="eyebrow">Admin console</span>
          <h2>管理员控制台</h2>
        </div>
        <p>创建销售账号、调整配额、管理发件账号和 warmup 状态。</p>
      </div>
      {(message || error) && <div className={`admin-alert ${error ? "is-error" : ""}`}>{error || message}</div>}
      <div className="admin-summary">
        <SummaryCard label="销售账号" value={`${summary.activeUsers}/${users.length}`} hint="启用 / 全部" />
        <SummaryCard label="今日获客" value={summary.sourceUsed} hint="全员已使用" />
        <SummaryCard label="今日发信" value={summary.sendUsed} hint="全员已发送" />
        <SummaryCard label="发件账号" value={`${summary.activeSenders}/${senders.length}`} hint="启用 / 全部" />
        <SummaryCard label="待改密码" value={summary.mustChange} hint="首次登录未完成" />
      </div>
      <div className="admin-grid">
        <section className="admin-card">
          <h3>新增销售账号</h3>
          <div className="form-grid compact">
            <label>
              账号
              <input value={newUser.username} onChange={(event) => setNewUser({ ...newUser, username: event.target.value })} placeholder="sales01" />
            </label>
            <label>
              姓名
              <input value={newUser.display_name} onChange={(event) => setNewUser({ ...newUser, display_name: event.target.value })} placeholder="销售01" />
            </label>
            <label>
              密码
              <input type="password" value={newUser.password} onChange={(event) => setNewUser({ ...newUser, password: event.target.value })} placeholder="强密码" />
            </label>
            <label>
              Reply-To
              <input value={newUser.reply_to_email} onChange={(event) => setNewUser({ ...newUser, reply_to_email: event.target.value })} placeholder="name@vertu.cn" />
            </label>
            <label>
              获客配额
              <input type="number" value={newUser.daily_source_limit} onChange={(event) => setNewUser({ ...newUser, daily_source_limit: event.target.value })} />
            </label>
            <label>
              发信配额
              <input type="number" value={newUser.daily_send_limit} onChange={(event) => setNewUser({ ...newUser, daily_send_limit: event.target.value })} />
            </label>
          </div>
          <div className="panel-actions">
            <button className="primary" type="button" onClick={createUser}>
              创建账号
            </button>
            <button type="button" onClick={loadAdminData}>
              刷新
            </button>
          </div>
        </section>
        <section className="admin-card">
          <h3>用户与配额</h3>
          {loading ? <div className="empty-state">正在加载...</div> : <UserTable users={users} onUpdate={updateUser} onResetPassword={resetPassword} />}
        </section>
        <section className="admin-card">
          <h3>发件账号池</h3>
          <SenderTable senders={senders} onUpdate={updateSender} />
        </section>
        <section className="admin-card audit-card">
          <div className="card-title-row">
            <h3>最近操作记录</h3>
            <button type="button" onClick={loadAdminData}>刷新日志</button>
          </div>
          <AuditLogTable logs={auditLogs} />
        </section>
      </div>
    </>
  );
}

function SummaryCard({ label, value, hint }) {
  return (
    <article>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{hint}</small>
    </article>
  );
}

function UserTable({ users, onUpdate, onResetPassword }) {
  const [drafts, setDrafts] = useState({});

  if (!users.length) return <div className="empty-state">暂无用户</div>;

  const valueFor = (user, field) => drafts[user.id]?.[field] ?? user[field];
  const updateDraft = (userId, field, value) => setDrafts((current) => ({ ...current, [userId]: { ...(current[userId] || {}), [field]: value } }));
  const save = (user) =>
    onUpdate(user.id, {
      daily_source_limit: Number(valueFor(user, "daily_source_limit") || 100),
      daily_send_limit: Number(valueFor(user, "daily_send_limit") || 100),
      reply_to_email: valueFor(user, "reply_to_email") || "",
    });

  return (
    <div className="admin-table">
      <table className="mini-table admin-data-table">
        <thead>
          <tr>
            <th>成员</th>
            <th>角色</th>
            <th>今日获客</th>
            <th>今日发信</th>
            <th>状态</th>
            <th>Reply-To</th>
            <th>配额设置</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {users.map((user) => (
            <tr key={user.id}>
              <td>
                <div className="admin-identity">
                  <strong>{user.display_name || user.username}</strong>
                  <span>{user.username} · ID {user.id}</span>
                </div>
              </td>
              <td><span className={`role-pill ${user.role === "admin" ? "role-admin" : ""}`}>{user.role}</span></td>
              <td><strong>{user.source_count_today || 0}</strong><span className="muted"> / {user.daily_source_limit}</span></td>
              <td><strong>{user.send_count_today || 0}</strong><span className="muted"> / {user.daily_send_limit}</span></td>
              <td>
                <span className={`status-pill ${user.active ? "is-active" : "is-paused"}`}>{user.active ? "启用" : "停用"}</span>
                {user.must_change_password && <span className="status-pill is-warning">待改密码</span>}
              </td>
              <td><input className="mini-input wide" value={valueFor(user, "reply_to_email") || ""} onChange={(event) => updateDraft(user.id, "reply_to_email", event.target.value)} placeholder="name@vertu.cn" /></td>
              <td>
                <div className="quota-edit">
                  <label>获客<input className="mini-input" type="number" value={valueFor(user, "daily_source_limit")} onChange={(event) => updateDraft(user.id, "daily_source_limit", event.target.value)} /></label>
                  <label>发信<input className="mini-input" type="number" value={valueFor(user, "daily_send_limit")} onChange={(event) => updateDraft(user.id, "daily_send_limit", event.target.value)} /></label>
                </div>
              </td>
              <td className="row-actions">
                <button className="primary soft" type="button" onClick={() => save(user)}>保存</button>
                <button type="button" onClick={() => onUpdate(user.id, { active: !user.active })}>{user.active ? "停用" : "启用"}</button>
                <button type="button" onClick={() => onResetPassword(user.id)}>重置密码</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SenderTable({ senders, onUpdate }) {
  const [drafts, setDrafts] = useState({});

  if (!senders.length) return <div className="empty-state">暂无发件账号。先在 config.yaml 的 sender_pool.accounts[] 配置。</div>;

  const valueFor = (sender, field) => drafts[sender.id]?.[field] ?? sender[field];
  const updateDraft = (senderId, field, value) => setDrafts((current) => ({ ...current, [senderId]: { ...(current[senderId] || {}), [field]: value } }));
  const save = (sender) =>
    onUpdate(sender.id, {
      daily_limit: Number(valueFor(sender, "daily_limit") || 100),
      warmup_stage: valueFor(sender, "warmup_stage"),
    });

  return (
    <div className="admin-table">
      <table className="mini-table admin-data-table">
        <thead>
          <tr>
            <th>发件身份</th>
            <th>Provider</th>
            <th>今日发送</th>
            <th>每日上限</th>
            <th>Warmup</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {senders.map((sender) => (
            <tr key={sender.id}>
              <td>
                <div className="admin-identity">
                  <strong>{sender.name}</strong>
                  <span>{sender.email} · ID {sender.id}</span>
                </div>
              </td>
              <td>{sender.provider}</td>
              <td><strong>{sender.send_count_today || 0}</strong><span className="muted"> / {sender.daily_limit}</span></td>
              <td><input className="mini-input" type="number" value={valueFor(sender, "daily_limit")} onChange={(event) => updateDraft(sender.id, "daily_limit", event.target.value)} /></td>
              <td>
                <select className="mini-input" value={valueFor(sender, "warmup_stage")} onChange={(event) => updateDraft(sender.id, "warmup_stage", event.target.value)}>
                  <option value="warmup">warmup</option>
                  <option value="production">production</option>
                </select>
              </td>
              <td><span className={`status-pill ${sender.active ? "is-active" : "is-paused"}`}>{sender.active ? "启用" : "停用"}</span></td>
              <td className="row-actions">
                <button className="primary soft" type="button" onClick={() => save(sender)}>保存</button>
                <button type="button" onClick={() => onUpdate(sender.id, { active: !sender.active })}>{sender.active ? "停用" : "启用"}</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AuditLogTable({ logs }) {
  if (!logs.length) return <div className="empty-state">暂无操作记录。上线后会记录登录、导入、获客、富化、发信和客户推进。</div>;

  return (
    <div className="admin-table">
      <table className="mini-table admin-data-table audit-table">
        <thead>
          <tr>
            <th>时间</th>
            <th>操作人</th>
            <th>动作</th>
            <th>对象</th>
            <th>结果</th>
            <th>摘要</th>
            <th>IP</th>
          </tr>
        </thead>
        <tbody>
          {logs.map((log) => (
            <tr key={log.id}>
              <td>{formatDate(log.created_at)}</td>
              <td>
                <div className="admin-identity compact">
                  <strong>{log.display_name || log.username || "系统"}</strong>
                  <span>{log.username || "-"} · {log.role || "-"}</span>
                </div>
              </td>
              <td><span className="audit-action">{auditActionLabel(log.action)}</span></td>
              <td>{[log.target_type, log.target_id].filter(Boolean).join(" #") || "-"}</td>
              <td><span className={`status-pill ${log.success ? "is-active" : "is-paused"}`}>{log.success ? "成功" : "失败"}</span></td>
              <td>
                <div className="audit-summary-cell">
                  <strong>{log.summary || "-"}</strong>
                  {log.error && <span>{log.error}</span>}
                </div>
              </td>
              <td>{log.ip_address || "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function auditActionLabel(action) {
  const labels = {
    login: "登录",
    change_password: "改密码",
    create_contact: "新增客户",
    import_csv: "CSV 导入",
    import_company_seeds: "种子导入",
    source: "自动获客",
    linkedin_public_search: "LinkedIn 搜索",
    promote_search_result: "候选入库",
    adopt_email_candidate: "采用邮箱",
    claim_public_contact: "领取客户",
    return_contact_to_public: "退回客户",
    auto_assign_public_pool: "自动分配",
    recycle_stale_private_pool: "回收客户",
    enrich: "富化邮箱",
    enrich_one: "单个富化",
    social_enrich: "富化社媒",
    social_enrich_one: "单个社媒",
    queue: "加入队列",
    queue_one: "单个入队",
    send: "批量发信",
    send_one: "单封发信",
    send_custom: "自定义发信",
    mark_contact: "改状态",
    update_lifecycle: "生命周期",
    add_lifecycle_activity: "跟进记录",
    profile_agent: "客户画像",
    stage_agent: "AI 分析",
    email_draft: "邮件草稿",
    admin_create_user: "创建账号",
    admin_update_user: "更新账号",
    admin_update_sender: "更新发件",
    blacklist: "黑名单",
    migrate: "数据库迁移",
    scheduler: "调度",
  };
  return labels[action] || action || "-";
}
