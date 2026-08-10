import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "./api.js";
import AcquisitionControl from "./AcquisitionControl.jsx";

const emptyNewUser = {
  username: "",
  display_name: "",
  password: "",
  reply_to_email: "",
  sender_alias_localpart: "",
  role: "sales",
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
  const [user, setUser] = useState(() => window.SALESBOT_SESSION?.user || null);
  const [users, setUsers] = useState([]);
  const [senders, setSenders] = useState([]);
  const [auditLogs, setAuditLogs] = useState([]);
  const [automationRuns, setAutomationRuns] = useState([]);
  const [regionRules, setRegionRules] = useState([]);
  const [qualityData, setQualityData] = useState(null);
  const [acquisitionPlans, setAcquisitionPlans] = useState([]);
  const [flywheelData, setFlywheelData] = useState(null);
  const [newUser, setNewUser] = useState(emptyNewUser);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [activeSection, setActiveSection] = useState("users");

  const isAdmin = user?.role === "admin";

  const loadAdminData = useCallback(async () => {
    if (!isAdmin) return;
    setLoading(true);
    setError("");
    try {
      const results = await Promise.allSettled([
        api("/api/admin/users"),
        api("/api/admin/senders"),
        api("/api/admin/audit-logs?limit=100"),
        api("/api/automation-runs"),
        api("/api/admin/region-rules"),
        api("/api/outbound-quality"),
        api("/api/admin/acquisition-plans"),
        api("/api/flywheel"),
      ]);
      const [userResult, senderResult, auditResult, runResult, ruleResult, qualityResult, planResult, flywheelResult] = results;
      if (userResult.status === "fulfilled") setUsers(userResult.value.users || []);
      if (senderResult.status === "fulfilled") setSenders(senderResult.value.senders || []);
      if (auditResult.status === "fulfilled") setAuditLogs(auditResult.value.logs || []);
      if (runResult.status === "fulfilled") setAutomationRuns(runResult.value.runs || []);
      if (ruleResult.status === "fulfilled") setRegionRules(ruleResult.value.rules || []);
      if (qualityResult.status === "fulfilled") setQualityData(qualityResult.value || null);
      if (planResult.status === "fulfilled") setAcquisitionPlans(planResult.value.plans || []);
      if (flywheelResult.status === "fulfilled") setFlywheelData(flywheelResult.value || null);
      const failures = results.filter((result) => result.status === "rejected");
      if (failures.length) setError(failures.map((result) => result.reason?.message || "管理数据加载失败").join("；"));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [isAdmin]);

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
      activeRuns: automationRuns.filter((item) => ["queued", "running"].includes(item.status)).length,
    };
  }, [users, senders, automationRuns]);

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
          role: newUser.role,
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

  async function updateAutomationRun(runId, action) {
    setError("");
    try {
      await api("/api/automation-runs/action", { method: "POST", body: JSON.stringify({ run_id: runId, action }) });
      setMessage(action === "pause" ? "任务已请求暂停" : "任务已恢复");
      await loadAdminData();
    } catch (err) {
      setError(err.message);
    }
  }

  async function saveRegionRules() {
    setError("");
    try {
      const rules = regionRules.map((rule) => ({
        owner: rule.owner,
        match: Array.isArray(rule.match) ? rule.match : String(rule.match || "").split(",").map((item) => item.trim()).filter(Boolean),
      }));
      const result = await api("/api/admin/region-rules", { method: "POST", body: JSON.stringify({ rules }) });
      setRegionRules(result.rules || []);
      setMessage("地区分配规则已保存");
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
      {(message || error) && <div className={`admin-alert ${error ? "is-error" : ""}`} role={error ? "alert" : "status"} aria-live={error ? "assertive" : "polite"}>{error || message}</div>}
      <div className="admin-summary">
        <SummaryCard label="销售账号" value={`${summary.activeUsers}/${users.length}`} hint="启用 / 全部" />
        <SummaryCard label="今日获客" value={summary.sourceUsed} hint="全员已使用" />
        <SummaryCard label="今日发信" value={summary.sendUsed} hint="全员已发送" />
        <SummaryCard label="发件账号" value={`${summary.activeSenders}/${senders.length}`} hint="启用 / 全部" />
        <SummaryCard label="待改密码" value={summary.mustChange} hint="首次登录未完成" />
        <SummaryCard label="运行任务" value={summary.activeRuns} hint="排队 / 处理中" />
      </div>
      <nav className="admin-section-nav" aria-label="管理员功能">
        {[
          ["users", "账号与配额"],
          ["senders", "发件账号"],
          ["automation", "获客任务"],
          ["acquisition", "获客与飞轮"],
          ["quality", "质量与实验"],
          ["assignment", "地区分配"],
          ["audit", "操作记录"],
        ].map(([value, label]) => <button key={value} type="button" className={activeSection === value ? "active" : ""} aria-pressed={activeSection === value} onClick={() => setActiveSection(value)}>{label}</button>)}
      </nav>
      <div className="admin-grid">
        {activeSection === "users" && <section className="admin-card">
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
              角色
              <select value={newUser.role} onChange={(event) => setNewUser({ ...newUser, role: event.target.value })}>
                <option value="sales">销售</option>
                <option value="manager">经理</option>
              </select>
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
              发件别名
              <input value={newUser.sender_alias_localpart} onChange={(event) => setNewUser({ ...newUser, sender_alias_localpart: event.target.value })} placeholder="viki（留空则使用账号名）" />
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
        </section>}
        {activeSection === "users" && <section className="admin-card admin-user-card">
          <h3>用户与配额</h3>
          {loading ? <div className="empty-state">正在加载...</div> : <UserTable users={users} onUpdate={updateUser} onResetPassword={resetPassword} />}
        </section>}
        {activeSection === "senders" && <section className="admin-card">
          <h3>发件账号池</h3>
          <SenderTable senders={senders} onUpdate={updateSender} />
        </section>}
        {activeSection === "automation" && <section className="admin-card automation-admin-card">
          <div className="card-title-row"><h3>后台获客任务</h3><button type="button" onClick={loadAdminData}>刷新任务</button></div>
          <AutomationRunTable runs={automationRuns} onAction={updateAutomationRun} />
        </section>}
        {activeSection === "acquisition" && <AcquisitionControl users={users} plans={acquisitionPlans} flywheel={flywheelData} onRefresh={loadAdminData} />}
        {activeSection === "quality" && <QualityExperimentPanel data={qualityData} onRefresh={loadAdminData} />}
        {activeSection === "assignment" && <section className="admin-card automation-admin-card">
          <div className="card-title-row"><div><h3>地区自动分配</h3><p className="muted">获客任务完成后，按国家/地区关键词首次分配到销售私人池。</p></div><button type="button" onClick={() => setRegionRules((current) => [...current, { owner: "", match: [] }])}>新增规则</button></div>
          <RegionRulesEditor rules={regionRules} users={users} onChange={setRegionRules} onSave={saveRegionRules} />
        </section>}
        {activeSection === "audit" && <section className="admin-card audit-card">
          <div className="card-title-row">
            <h3>最近操作记录</h3>
            <button type="button" onClick={loadAdminData}>刷新日志</button>
          </div>
          <AuditLogTable logs={auditLogs} />
        </section>}
      </div>
    </>
  );
}

function QualityExperimentPanel({ data, onRefresh }) {
  const [form, setForm] = useState({
    name: "",
    hypothesis: "",
    variable_name: "subject",
    variant_a: "",
    variant_b: "",
  });
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const lead = data?.lead_quality || {};
  const copy = data?.copy_quality || {};
  const replies = data?.replies || {};
  const calibration = data?.icp_calibration || {};
  const profile = data?.icp_profile || {};
  const experiments = data?.experiments || [];

  async function createExperiment() {
    if (!form.name.trim() || !form.hypothesis.trim() || !form.variant_a.trim() || !form.variant_b.trim()) {
      setNotice("请填写实验名称、假设和两个版本的差异。");
      return;
    }
    setBusy(true);
    setNotice("");
    try {
      await api("/api/admin/experiments", {
        method: "POST",
        body: JSON.stringify({
          action: "create",
          name: form.name.trim(),
          hypothesis: form.hypothesis.trim(),
          variable_name: form.variable_name,
          variants: [
            { name: "A", instruction: form.variant_a.trim() },
            { name: "B", instruction: form.variant_b.trim() },
          ],
          status: "active",
        }),
      });
      setForm({ name: "", hypothesis: "", variable_name: "subject", variant_a: "", variant_b: "" });
      setNotice("实验已启动。新生成的邮件会按联系人稳定分组。");
      await onRefresh();
    } catch (err) {
      setNotice(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function updateExperiment(experimentId, status) {
    setBusy(true);
    setNotice("");
    try {
      await api("/api/admin/experiments", {
        method: "POST",
        body: JSON.stringify({ action: "update", experiment_id: experimentId, status }),
      });
      setNotice(status === "completed" ? "实验已结束，结果已保留。" : "实验状态已更新。");
      await onRefresh();
    } catch (err) {
      setNotice(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function applyIcpThreshold() {
    if (!profile.id || calibration.proposed_threshold == null) return;
    setBusy(true);
    setNotice("");
    try {
      await api("/api/admin/icp-profile", {
        method: "POST",
        body: JSON.stringify({
          profile_id: profile.id,
          qualified_threshold: calibration.proposed_threshold,
        }),
      });
      setNotice(`ICP 合格线已调整为 ${calibration.proposed_threshold} 分。`);
      await onRefresh();
    } catch (err) {
      setNotice(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="admin-card quality-admin-card">
      <div className="card-title-row">
        <div>
          <h3>出站质量闭环</h3>
          <p className="muted">用人工反馈校准 ICP，用有效回复而不是打开率评估名单和文案。</p>
        </div>
        <button type="button" onClick={onRefresh}>刷新</button>
      </div>
      {notice && <div className="admin-alert">{notice}</div>}
      <div className="quality-dashboard-grid">
        <QualityMetric label="平均 ICP" value={`${lead.average_score || 0} 分`} hint={`高优先 ${lead.priority || 0} / 合格 ${lead.qualified || 0}`} />
        <QualityMetric label="名单待复核" value={lead.review || 0} hint={`不建议 ${lead.disqualified || 0}`} />
        <QualityMetric label="文案通过" value={copy.ready || 0} hint={`需改 ${copy.revise || 0} / 拦截 ${copy.blocked || 0}`} />
        <QualityMetric label="有效回复率" value={`${replies.positive_reply_rate || 0}%`} hint={`有效 ${replies.positive || 0} / 总回复 ${replies.total || 0}`} />
        <QualityMetric label="负向回复" value={replies.negative || 0} hint={`自动回复 ${replies.ooo || 0} / 退订 ${replies.unsubscribe || 0}`} />
      </div>

      <div className="quality-admin-columns">
        <section className="quality-panel">
          <div className="quality-panel-title">
            <div><strong>ICP 调优</strong><span>{profile.name || "默认客户画像"}</span></div>
            <b>{calibration.accuracy || 0}% 准确</b>
          </div>
          <dl className="quality-definition-list">
            <div><dt>人工复核</dt><dd>{calibration.reviewed || 0} 条</dd></div>
            <div><dt>误判为合格</dt><dd>{calibration.false_positive || 0}</dd></div>
            <div><dt>漏掉好线索</dt><dd>{calibration.false_negative || 0}</dd></div>
            <div><dt>当前 / 建议合格线</dt><dd>{calibration.current_threshold || 70} / {calibration.proposed_threshold || 70}</dd></div>
          </dl>
          <p className="quality-recommendation">{calibrationRecommendation(calibration.recommendation)}</p>
          <button type="button" disabled={busy || calibration.reviewed < 10 || calibration.current_threshold === calibration.proposed_threshold} onClick={applyIcpThreshold}>
            应用建议合格线
          </button>
        </section>

        <section className="quality-panel experiment-builder">
          <div className="quality-panel-title"><div><strong>新建单变量实验</strong><span>同一轮只测试一个差异</span></div></div>
          <label>实验名称<input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="例如：问题式主题 vs 价值式主题" /></label>
          <label>假设<input value={form.hypothesis} onChange={(event) => setForm({ ...form, hypothesis: event.target.value })} placeholder="例如：具体业务问题能提高有效回复率" /></label>
          <label>测试变量
            <select value={form.variable_name} onChange={(event) => setForm({ ...form, variable_name: event.target.value })}>
              <option value="subject">邮件主题</option>
              <option value="opening">开场角度</option>
              <option value="cta">行动指令</option>
              <option value="length">正文长度</option>
            </select>
          </label>
          <div className="experiment-variants">
            <label>A 版本<input value={form.variant_a} onChange={(event) => setForm({ ...form, variant_a: event.target.value })} placeholder="保持当前写法" /></label>
            <label>B 版本<input value={form.variant_b} onChange={(event) => setForm({ ...form, variant_b: event.target.value })} placeholder="使用具体业务问题" /></label>
          </div>
          <button type="button" className="primary" disabled={busy} onClick={createExperiment}>启动实验</button>
        </section>
      </div>

      <section className="experiment-history">
        <div className="quality-panel-title"><div><strong>实验复盘</strong><span>每个版本至少发送 100 封后再判断胜出</span></div></div>
        {!experiments.length ? <div className="empty-state">尚未创建实验</div> : experiments.map((experiment) => (
          <article key={experiment.id}>
            <header>
              <div><strong>{experiment.name}</strong><span>{experiment.variable_name} · {experiment.hypothesis}</span></div>
              <em>{experimentStatusLabel(experiment.status)}</em>
            </header>
            <div className="experiment-results">
              {(experiment.analysis?.variants || []).map((variant) => (
                <div key={variant.name}>
                  <b>{variant.name}</b>
                  <span>发送 {variant.sent}</span>
                  <span>回复 {variant.replies}</span>
                  <span>有效回复 {variant.positive_reply_rate}%</span>
                </div>
              ))}
            </div>
            <p>{experiment.analysis?.winner ? `胜出版本：${experiment.analysis.winner}` : "样本仍在积累，暂不下结论。"}</p>
            {experiment.status === "active" && <button type="button" disabled={busy} onClick={() => updateExperiment(experiment.id, "completed")}>结束并保留结果</button>}
          </article>
        ))}
      </section>
    </section>
  );
}

function QualityMetric({ label, value, hint }) {
  return <article className="quality-metric"><span>{label}</span><strong>{value}</strong><small>{hint}</small></article>;
}

function calibrationRecommendation(value) {
  const labels = {
    "Collect at least 10 reviewed examples before changing the ICP threshold.": "至少收集 10 条销售复核结果后，再调整 ICP 合格线。",
    "Tighten the ICP threshold by 5 points and review the repeated false-positive traits.": "误判偏多，建议提高 5 分，并复查重复出现的错误特征。",
    "Relax the ICP threshold by 5 points and review the missed positive traits.": "漏掉好线索偏多，建议降低 5 分，并补充遗漏的正向特征。",
    "Keep the current threshold; reviewed errors are balanced.": "当前误差较均衡，建议保持现有合格线。",
  };
  return labels[value] || value || "等待销售反馈。";
}

function experimentStatusLabel(status) {
  return { draft: "草稿", active: "进行中", completed: "已完成", cancelled: "已取消" }[status] || status;
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
      sender_alias_localpart: valueFor(user, "sender_alias_localpart") || "",
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
            <th>企业邮箱 / 发件别名</th>
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
              <td>
                <input aria-label={`${user.display_name || user.username} 的回复邮箱`} className="mini-input wide" value={valueFor(user, "reply_to_email") || ""} onChange={(event) => updateDraft(user.id, "reply_to_email", event.target.value)} placeholder="name@vertu.cn" />
                <input aria-label={`${user.display_name || user.username} 的发件别名`} className="mini-input wide" value={valueFor(user, "sender_alias_localpart") || ""} onChange={(event) => updateDraft(user.id, "sender_alias_localpart", event.target.value)} placeholder="发件别名（默认账号名）" />
              </td>
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

function AutomationRunTable({ runs, onAction }) {
  if (!runs.length) return <div className="empty-state">暂无后台获客任务</div>;
  return (
    <div className="admin-table">
      <table className="mini-table admin-data-table">
        <thead><tr><th>任务</th><th>销售</th><th>状态</th><th>进度</th><th>入库/候选</th><th>错误</th><th>操作</th></tr></thead>
        <tbody>{runs.map((run) => {
          const result = run.result || {};
          return <tr key={run.id}>
            <td><strong>#{run.id}</strong><div className="muted">{run.run_type}</div></td>
            <td>{run.display_name || run.username || `ID ${run.owner_user_id}`}</td>
            <td><span className={`status-pill ${run.status === "failed" ? "is-paused" : ["running", "awaiting_approval"].includes(run.status) ? "is-active" : "is-warning"}`}>{automationRunLabel(run.status)}</span></td>
            <td>{run.progress_current || 0}/{run.progress_total || 0}</td>
            <td>{result.promoted || 0} / {result.results || 0}</td>
            <td><span className="automation-error" title={run.error || ""}>{run.error || "-"}</span></td>
            <td className="row-actions">{run.status === "running" && <button type="button" onClick={() => onAction(run.id, "pause")}>暂停</button>}{["paused", "failed"].includes(run.status) && <button type="button" className="primary soft" onClick={() => onAction(run.id, run.status === "failed" ? "retry" : "resume")}>{run.status === "failed" ? "重试" : "继续"}</button>}</td>
          </tr>;
        })}</tbody>
      </table>
    </div>
  );
}

function RegionRulesEditor({ rules, users, onChange, onSave }) {
  const salesUsers = users.filter((user) => user.active && user.role === "sales");
  function patchRule(index, patch) {
    onChange(rules.map((rule, ruleIndex) => ruleIndex === index ? { ...rule, ...patch } : rule));
  }
  if (!rules.length) {
    return <div className="region-rules-empty"><span>尚未配置。新线索会留在公共池，由销售自行领取。</span><button type="button" className="primary" onClick={onSave}>保存空规则</button></div>;
  }
  return <div className="region-rules-editor">
    {rules.map((rule, index) => <div className="region-rule-row" key={`${index}-${rule.owner}`}>
      <label>销售<select value={rule.owner || ""} onChange={(event) => patchRule(index, { owner: event.target.value })}><option value="">选择销售</option>{salesUsers.map((user) => <option key={user.id} value={user.username}>{user.display_name || user.username}</option>)}</select></label>
      <label>匹配地区<input value={(rule.match || []).join(", ")} onChange={(event) => patchRule(index, { match: event.target.value.split(",").map((item) => item.trim()).filter(Boolean) })} placeholder="uae, dubai, qatar, kuwait" /></label>
      <button type="button" className="danger-button" onClick={() => onChange(rules.filter((_, ruleIndex) => ruleIndex !== index))}>删除</button>
    </div>)}
    <div className="panel-actions"><button type="button" className="primary" onClick={onSave}>保存分配规则</button></div>
  </div>;
}

function automationRunLabel(status) {
  return { queued: "排队中", running: "处理中", paused: "已暂停", failed: "失败", awaiting_approval: "待核验", completed: "完成" }[status] || status;
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
