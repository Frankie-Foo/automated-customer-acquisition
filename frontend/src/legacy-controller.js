const state = {
  status: "",
  filter: "",
  search: "",
  user: null,
  usage: null,
  linkedinTaskId: null,
};

const statusOrder = ["new", "enriched", "queued", "sent_1", "sent_2", "sent_3", "replied", "bounced", "unsubscribed"];

const notice = document.querySelector("#notice");
const metrics = document.querySelector("#metrics");
const opsReportContent = document.querySelector("#ops-report-content");
const adminConsole = document.querySelector("#admin-console");
const adminSummary = document.querySelector("#admin-summary");
const adminUsersTable = document.querySelector("#admin-users-table");
const adminSendersTable = document.querySelector("#admin-senders-table");
const followupGrid = document.querySelector("#followup-grid");
const lifecycleGrid = document.querySelector("#lifecycle-grid");
const workspaceEmpty = document.querySelector("#workspace-empty");
const workspaceContent = document.querySelector("#workspace-content");
const workspaceProfile = document.querySelector("#workspace-profile");
const activityList = document.querySelector("#activity-list");
const stageAnalysis = document.querySelector("#stage-analysis");
const contactsBody = document.querySelector("#contacts-body");
const readinessNode = document.querySelector("#readiness");
const readyPill = document.querySelector("#ready-pill");
const loginScreen = document.querySelector("#login-screen");
const loginForm = document.querySelector("#login-form");
const loginError = document.querySelector("#login-error");
const passwordChangeForm = document.querySelector("#password-change-form");
const passwordChangeError = document.querySelector("#password-change-error");
const accountName = document.querySelector("#account-name");
const quotaStatus = document.querySelector("#quota-status");
const logoutButton = document.querySelector("#logout-button");
const linkedinSearchOutput = document.querySelector("#linkedin-search-output");

function showNotice(message, type = "") {
  notice.textContent = message;
  notice.className = `notice ${type}`.trim();
  notice.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function hideNotice() {
  notice.className = "notice hidden";
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (response.status === 401 && path !== "/api/login") {
    showLogin();
    window.dispatchEvent(new CustomEvent("salesbot:unauthorized"));
    throw new Error("请先登录");
  }
  if (!data.ok) throw new Error(data.error || "请求失败");
  return data.data;
}

async function loadSession() {
  if (window.SALESBOT_REACT_AUTH) return;
  try {
    const session = await api("/api/me");
    state.user = session.user;
    state.usage = session.usage;
    renderAccount();
    if (state.user.must_change_password) {
      showPasswordChange();
      return;
    }
    hideLogin();
    await refresh();
  } catch (error) {
    showLogin();
  }
}

function showLogin() {
  if (window.SALESBOT_REACT_AUTH) {
    loginScreen?.classList.remove("hidden");
    return;
  }
  loginScreen.classList.remove("hidden");
  loginForm.classList.remove("hidden");
  passwordChangeForm.classList.add("hidden");
}

function hideLogin() {
  if (window.SALESBOT_REACT_AUTH) {
    loginScreen?.classList.add("hidden");
    return;
  }
  loginScreen.classList.add("hidden");
  loginError.textContent = "";
  passwordChangeError.textContent = "";
}

function showPasswordChange() {
  if (window.SALESBOT_REACT_AUTH) {
    loginScreen?.classList.remove("hidden");
    return;
  }
  loginScreen.classList.remove("hidden");
  loginForm.classList.add("hidden");
  passwordChangeForm.classList.remove("hidden");
  passwordChangeError.textContent = "";
}

function renderAccount() {
  if (!state.user) {
    accountName.textContent = "未登录";
    quotaStatus.textContent = "今日配额 --";
    dispatchSession();
    return;
  }
  const usage = state.usage || {};
  accountName.textContent = state.user.display_name || state.user.username;
  quotaStatus.textContent = `获客 ${usage.source_count || 0}/${state.user.daily_source_limit} · 发信 ${usage.send_count || 0}/${state.user.daily_send_limit}`;
  adminConsole.classList.toggle("hidden", state.user.role !== "admin");
  dispatchSession();
}

function updateUsage(usage) {
  if (!usage) return;
  state.usage = usage;
  renderAccount();
}

function dispatchSession() {
  if (window.SALESBOT_REACT_AUTH) return;
  window.dispatchEvent(new CustomEvent("salesbot:session", { detail: { user: state.user, usage: state.usage } }));
}

window.addEventListener("salesbot:session", (event) => {
  state.user = event.detail?.user || null;
  state.usage = event.detail?.usage || null;
  renderAccount();
  if (state.user && !state.user.must_change_password) {
    hideLogin();
  } else if (!state.user) {
    showLogin();
  }
});

window.addEventListener("salesbot:refresh", refresh);
window.addEventListener("salesbot:refresh-related", async () => {
  try {
    const [summary, lifecycle] = await Promise.all([api("/api/summary"), api("/api/lifecycle")]);
    renderMetrics(summary);
    renderLifecycle(lifecycle, window.latestContacts || []);
    await refreshOpsReport();
    await refreshAdminConsole();
  } catch (error) {
    showNotice(error.message, "error");
  }
});
window.addEventListener("salesbot:usage", (event) => updateUsage(event.detail?.usage));
window.addEventListener("salesbot:contacts-updated", (event) => {
  const rows = event.detail?.contacts || [];
  window.latestContacts = rows;
  renderFollowups(rows);
  api("/api/lifecycle").then((lifecycle) => renderLifecycle(lifecycle, rows)).catch(() => {});
});
window.addEventListener("salesbot:open-contact", async (event) => {
  try {
    await loadCustomerWorkspace(Number(event.detail?.contactId));
    showNotice("客户详情已打开");
  } catch (error) {
    showNotice(error.message, "error");
  }
});

window.addEventListener("salesbot:notice", (event) => {
  if (event.detail?.message) showNotice(event.detail.message, event.detail.type || "");
});

async function refresh() {
  try {
    const [summary, contacts, lifecycle] = await Promise.all([
      api("/api/summary"),
      api(`/api/contacts?status=${encodeURIComponent(state.status)}&filter=${encodeURIComponent(state.filter)}&search=${encodeURIComponent(state.search)}&limit=100`),
      api("/api/lifecycle"),
    ]);
    hideNotice();
    renderMetrics(summary);
    const rows = contacts.contacts || [];
    window.latestContacts = rows;
    renderFollowups(rows);
    renderLifecycle(lifecycle, rows);
    renderContacts(rows);
    await refreshOpsReport();
    await refreshAdminConsole();
    await refreshLinkedInSearchTasks();
    refreshReadiness();
  } catch (error) {
    renderMetrics({ total_contacts: 0, sent_today: 0, statuses: {}, events_7d: {} });
    renderFollowups([]);
    renderLifecycle({ stages: {}, outreach: {}, actions: [] }, []);
    renderContacts([]);
    renderOpsReport({});
    showNotice(`数据库还不可用：${error.message}。先确认 .env/config.yaml，然后点“初始化/迁移数据库”。`, "error");
  }
}

async function refreshOpsReport() {
  try {
    renderOpsReport(await api("/api/ops-report"));
  } catch {
    renderOpsReport({});
  }
}

window.addEventListener("salesbot:ops-refresh", refreshOpsReport);

async function refreshAdminConsole() {
  if (window.SALESBOT_REACT_ADMIN) return;
  if (state.user?.role !== "admin") return;
  try {
    const [users, senders] = await Promise.all([
      api("/api/admin/users"),
      api("/api/admin/senders"),
    ]);
    renderAdminSummary(users.users || [], senders.senders || []);
    renderAdminUsers(users.users || []);
    renderAdminSenders(senders.senders || []);
  } catch (error) {
    adminUsersTable.innerHTML = `<div class="empty-state">管理员数据加载失败：${escapeHtml(error.message)}</div>`;
  }
}

async function refreshReadiness() {
  try {
    const data = await api("/api/readiness");
    readyPill.textContent = data.ready ? "Ready" : "Action needed";
    readyPill.className = data.ready ? "ready" : "missing";
    readinessNode.innerHTML = data.checks.map((check) => `
      <div class="check ${check.ok ? "ok" : "missing"}" title="${escapeHtml(check.message || "")}">
        <span>${escapeHtml(readinessLabel(check.name))}</span>
        <strong>${check.ok ? "OK" : (check.required ? "缺失" : "可选")}</strong>
      </div>
    `).join("");
  } catch (error) {
    readyPill.textContent = "Error";
    readyPill.className = "missing";
    readinessNode.innerHTML = `<div class="check missing"><span>readiness</span><strong>失败</strong></div>`;
  }
}

function renderMetrics(summary) {
  const events = summary.events_7d || {};
  const cards = [
    ["客户总数", summary.total_contacts || 0, "系统内全部客户"],
    ["今日发送", summary.sent_today || 0, "当天真实/演练发送"],
    ["待发送", summary.statuses?.queued || 0, "已入队等待触达"],
    ["7天打开", events.opened || 0, "最近 7 天打开事件"],
    ["已回复", summary.statuses?.replied || 0, "需要销售跟进"],
  ];
  metrics.innerHTML = cards.map(([label, value, hint]) => `
    <div class="metric">
      <span>${label}</span>
      <strong>${value}</strong>
      <small>${hint}</small>
    </div>
  `).join("");
}

function readinessLabel(name) {
  return {
    database: "数据库连接",
    lead_source: "自动获客 API",
    enrichment: "邮箱富化 API",
    resend: "邮件发送 API",
    sender_email: "发件邮箱域名",
    dry_run: "真实发送开关",
    public_url: "公网访问地址",
    admin_password: "管理员密码",
    social_enrichment: "社媒富化 API",
    llm: "AI 文案模型",
    slack: "Slack 通知",
  }[name] || name;
}

function renderFollowups(contacts) {
  const openedNoReply = contacts.filter((contact) =>
    Number(contact.opened_count || 0) > 0 && !["replied", "bounced", "unsubscribed"].includes(contact.status)
  );
  const replied = contacts.filter((contact) => contact.status === "replied" || Number(contact.replied_count || 0) > 0);
  const bounced = contacts.filter((contact) => contact.status === "bounced" || Number(contact.bounced_count || 0) > 0);
  const cards = [
    {
      title: "已打开未回复",
      count: openedNoReply.length,
      hint: "建议今天人工跟进或准备下一封",
      tone: "hot",
      contacts: openedNoReply,
    },
    {
      title: "已回复",
      count: replied.length,
      hint: "需要销售马上接手沟通",
      tone: "reply",
      contacts: replied,
    },
    {
      title: "退信需处理",
      count: bounced.length,
      hint: "检查邮箱质量或加入黑名单",
      tone: "risk",
      contacts: bounced,
    },
  ];
  followupGrid.innerHTML = cards.map(renderFollowupCard).join("");
}

function renderFollowupCard(card) {
  const items = card.contacts.slice(0, 3).map((contact) => `
    <li>
      <div>
        <strong>${escapeHtml(fullName(contact))}</strong>
        <span>${escapeHtml(contact.company_name || contact.company_domain || "")}</span>
      </div>
      <em>${escapeHtml(followupMeta(contact))}</em>
    </li>
  `).join("");
  const empty = `<li class="empty-task">当前没有需要处理的客户</li>`;
  return `
    <article class="followup-card ${card.tone}">
      <div class="followup-title">
        <div>
          <strong>${escapeHtml(card.title)}</strong>
          <span>${escapeHtml(card.hint)}</span>
        </div>
        <b>${card.count}</b>
      </div>
      <ul>${items || empty}</ul>
    </article>
  `;
}

function renderOpsReport(report) {
  const totals = report.totals || {};
  const events = report.events || {};
  const isAdminReport = (report.scope || "") === "team" || state.user?.role === "admin";
  const providerRows = (report.provider_stats || []).slice(0, 8).map((row) => `
    <tr>
      <td>${escapeHtml(row.provider)}</td>
      <td>${row.calls || 0}</td>
      <td>${row.candidates || 0}</td>
      <td>${row.valid_candidates || 0}</td>
      <td>${row.selected || 0}</td>
      <td>${row.errors || 0}</td>
    </tr>
  `).join("");
  const userRows = (report.by_user || []).map((user) => `
    <tr>
      <td>${escapeHtml(user.display_name || user.username)}</td>
      <td>${user.source_count_today || 0}/${user.daily_source_limit}</td>
      <td>${user.send_count_today || 0}/${user.daily_send_limit}</td>
      <td>${user.owned_contacts || 0}</td>
      <td>${user.active ? "启用" : "停用"}</td>
    </tr>
  `).join("");
  const failureRows = (report.failures || []).slice(0, 6).map((item) => `
    <li><span>${escapeHtml(item.reason)}</span><b>${item.count}</b></li>
  `).join("");
  opsReportContent.innerHTML = `
    <div class="ops-cards">
      ${renderOpsCard("今日新增线索", totals.new_contacts_today)}
      ${renderOpsCard("今日有效邮箱", totals.valid_emails_today)}
      ${renderOpsCard("今日发送", events.sent_today)}
      ${renderOpsCard("今日打开", events.opened_today)}
      ${renderOpsCard("今日回复", (totals.replied || 0) + (events.replied_events_today || 0))}
      ${renderOpsCard("今日退信", (totals.bounced || 0) + (events.bounced_events_today || 0))}
      ${renderOpsCard("今日需处理", (events.opened_no_reply || 0) + (totals.replied || 0) + (totals.bounced || 0))}
    </div>
    <div class="ops-grid">
      <section>
        <h3>销售配额日报</h3>
        <table class="mini-table"><thead><tr><th>销售</th><th>获客</th><th>发信</th><th>客户</th><th>状态</th></tr></thead><tbody>${userRows || "<tr><td colspan='5'>暂无数据</td></tr>"}</tbody></table>
      </section>
      <section>
        <h3>邮箱 Provider 统计</h3>
        <table class="mini-table"><thead><tr><th>Provider</th><th>调用</th><th>候选</th><th>Valid</th><th>选中</th><th>错误</th></tr></thead><tbody>${providerRows || "<tr><td colspan='6'>暂无数据</td></tr>"}</tbody></table>
      </section>
      <section>
        <h3>失败原因</h3>
        <ul class="failure-list">${failureRows || "<li><span>暂无失败</span><b>0</b></li>"}</ul>
      </section>
    </div>
  `;
  if (!isAdminReport) {
    const sections = opsReportContent.querySelectorAll(".ops-grid section");
    sections[1]?.remove();
  }
}

function renderOpsCard(label, value) {
  return `<article><span>${escapeHtml(label)}</span><strong>${Number(value || 0)}</strong></article>`;
}

function renderAdminSummary(users, senders) {
  if (!adminSummary) return;
  const activeUsers = users.filter((user) => user.active).length;
  const mustChange = users.filter((user) => user.must_change_password).length;
  const sourceUsed = users.reduce((sum, user) => sum + Number(user.source_count_today || 0), 0);
  const sendUsed = users.reduce((sum, user) => sum + Number(user.send_count_today || 0), 0);
  const activeSenders = senders.filter((sender) => sender.active).length;
  adminSummary.innerHTML = `
    <article>
      <span>销售账号</span>
      <strong>${activeUsers}/${users.length}</strong>
      <small>启用 / 全部</small>
    </article>
    <article>
      <span>今日获客</span>
      <strong>${sourceUsed}</strong>
      <small>全员已使用</small>
    </article>
    <article>
      <span>今日发信</span>
      <strong>${sendUsed}</strong>
      <small>全员已发送</small>
    </article>
    <article>
      <span>发件账号</span>
      <strong>${activeSenders}/${senders.length}</strong>
      <small>启用 / 全部</small>
    </article>
    <article>
      <span>待改密码</span>
      <strong>${mustChange}</strong>
      <small>首次登录未完成</small>
    </article>
  `;
}

function renderAdminUsers(users) {
  if (!users.length) {
    adminUsersTable.innerHTML = `<div class="empty-state">暂无用户</div>`;
    return;
  }
  adminUsersTable.innerHTML = `
    <table class="mini-table admin-data-table">
      <thead><tr><th>成员</th><th>角色</th><th>今日获客</th><th>今日发信</th><th>状态</th><th>配额设置</th><th>操作</th></tr></thead>
      <tbody>
        ${users.map((user) => `
          <tr>
            <td>
              <div class="admin-identity">
                <strong>${escapeHtml(user.display_name || user.username)}</strong>
                <span>${escapeHtml(user.username)} · ID ${user.id}</span>
              </div>
            </td>
            <td><span class="role-pill ${user.role === "admin" ? "role-admin" : ""}">${escapeHtml(user.role)}</span></td>
            <td><strong>${user.source_count_today || 0}</strong><span class="muted"> / ${user.daily_source_limit}</span></td>
            <td><strong>${user.send_count_today || 0}</strong><span class="muted"> / ${user.daily_send_limit}</span></td>
            <td>
              <span class="status-pill ${user.active ? "is-active" : "is-paused"}">${user.active ? "启用" : "停用"}</span>
              ${user.must_change_password ? `<span class="status-pill is-warning">待改密码</span>` : ""}
            </td>
            <td>
              <div class="quota-edit">
                <label>获客<input class="mini-input" data-user-source="${user.id}" type="number" value="${user.daily_source_limit}" /></label>
                <label>发信<input class="mini-input" data-user-send="${user.id}" type="number" value="${user.daily_send_limit}" /></label>
              </div>
            </td>
            <td class="row-actions">
              <button class="primary soft" data-admin-user-save="${user.id}">保存</button>
              <button data-admin-user-toggle="${user.id}" data-active="${user.active ? "false" : "true"}">${user.active ? "停用" : "启用"}</button>
              <button data-admin-user-reset="${user.id}">重置密码</button>
            </td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

function renderAdminSenders(senders) {
  if (!senders.length) {
    adminSendersTable.innerHTML = `<div class="empty-state">暂无发件账号。先在 config.yaml 的 sender_pool.accounts[] 配置。</div>`;
    return;
  }
  adminSendersTable.innerHTML = `
    <table class="mini-table admin-data-table">
      <thead><tr><th>发件身份</th><th>Provider</th><th>今日发送</th><th>每日上限</th><th>Warmup</th><th>状态</th><th>操作</th></tr></thead>
      <tbody>
        ${senders.map((sender) => `
          <tr>
            <td>
              <div class="admin-identity">
                <strong>${escapeHtml(sender.name)}</strong>
                <span>${escapeHtml(sender.email)} · ID ${sender.id}</span>
              </div>
            </td>
            <td>${escapeHtml(sender.provider)}</td>
            <td><strong>${sender.send_count_today || 0}</strong><span class="muted"> / ${sender.daily_limit}</span></td>
            <td><input class="mini-input" data-sender-limit="${sender.id}" type="number" value="${sender.daily_limit}" /></td>
            <td>
              <select class="mini-input" data-sender-warmup="${sender.id}">
                <option value="warmup" ${sender.warmup_stage === "warmup" ? "selected" : ""}>warmup</option>
                <option value="production" ${sender.warmup_stage === "production" ? "selected" : ""}>production</option>
              </select>
            </td>
            <td><span class="status-pill ${sender.active ? "is-active" : "is-paused"}">${sender.active ? "启用" : "停用"}</span></td>
            <td class="row-actions">
              <button class="primary soft" data-admin-sender-save="${sender.id}">保存</button>
              <button data-admin-sender-toggle="${sender.id}" data-active="${sender.active ? "false" : "true"}">${sender.active ? "停用" : "启用"}</button>
            </td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

const lifecycleStages = [
  ["lead", "陌生线索"],
  ["replied", "已回复"],
  ["conversation", "初步沟通"],
  ["meeting", "约会/会议"],
  ["business_plan", "商业计划"],
  ["store_visit", "到店参观"],
  ["trial_order", "试订单"],
  ["agency_agreement", "代理协议"],
  ["hq_visit", "总部拜访"],
  ["signed", "成功签约"],
  ["maintenance", "持续维护"],
  ["waiting_pool", "等待池"],
  ["abandoned", "已放弃"],
];

function renderLifecycle(lifecycle, contacts) {
  const stages = lifecycle.stages || {};
  lifecycleGrid.innerHTML = lifecycleStages.map(([key, label]) => {
    const count = stages[key] || 0;
    const examples = contacts.filter((contact) => contact.lifecycle_stage === key).slice(0, 2);
    const names = examples.map((contact) => `<span>${escapeHtml(fullName(contact))}</span>`).join("");
    return `
      <article class="lifecycle-card ${key}">
        <strong>${escapeHtml(label)}</strong>
        <b>${count}</b>
        <div>${names || "<span>暂无客户</span>"}</div>
      </article>
    `;
  }).join("");
}

function followupMeta(contact) {
  if (contact.status === "replied" || Number(contact.replied_count || 0) > 0) return "已回复";
  if (contact.status === "bounced" || Number(contact.bounced_count || 0) > 0) return "退信";
  if (Number(contact.opened_count || 0) > 0) return `打开 ${contact.opened_count} 次`;
  return eventLabel(contact.last_event_type || contact.status);
}

function renderContacts(contacts) {
  if (window.SALESBOT_REACT_CONTACTS) return;
  if (!contacts.length) {
    contactsBody.innerHTML = `
      <tr>
        <td colspan="13">
          <div class="empty-state">
            <strong>还没有客户</strong>
            <div>先用上方“自动获客”、CSV 导入，或手动新增一个联系人。</div>
          </div>
        </td>
      </tr>`;
    return;
  }
  contactsBody.innerHTML = contacts.map((contact) => `
    <tr>
      <td>${contact.id}</td>
      <td>
        <strong>${escapeHtml(fullName(contact))}</strong>
        <div class="muted">${escapeHtml(contact.job_title || "")}</div>
        ${renderLinkedInLink(contact)}
      </td>
      <td>
        <strong>${escapeHtml(contact.company_name || "")}</strong>
        <div class="muted">${escapeHtml(contact.company_domain || "")}</div>
      </td>
      <td>
        ${escapeHtml(displayEmail(contact))}
        <div class="muted">${escapeHtml(emailMeta(contact))}</div>
      </td>
      <td><span class="badge ${escapeHtml(contact.status)}">${escapeHtml(statusLabel(contact.status))}</span></td>
      <td>${contact.sequence_step || 0}</td>
      <td>${renderSocialProfiles(contact)}</td>
      <td>${renderEmailFeedback(contact)}</td>
      <td>${renderLifecycleCell(contact)}</td>
      <td>${renderProfileSummary(contact)}</td>
      <td>${formatDate(contact.last_contacted_at)}</td>
      <td>${renderRowActions(contact)}</td>
      <td class="error-text" title="${escapeHtml(contact.enrich_error || "")}">${escapeHtml(contact.enrich_error || "")}</td>
    </tr>
  `).join("");
}

function fullName(contact) {
  return [contact.first_name, contact.last_name].filter(Boolean).join(" ") || "(No name)";
}

function renderLinkedInLink(contact) {
  if (!isHttpUrl(contact.linkedin_url)) return "";
  return `<a class="profile-link" href="${escapeHtml(contact.linkedin_url)}" target="_blank" rel="noopener">LinkedIn</a>`;
}

function renderSocialProfiles(contact) {
  const profiles = contact.social_profiles || {};
  const labels = {
    linkedin: "LinkedIn",
    twitter: "X",
    github: "GitHub",
    facebook: "Facebook",
    website: "Website",
  };
  const links = Object.entries(labels)
    .filter(([key]) => isHttpUrl(profiles[key]))
    .map(([key, label]) => `<a class="social-link" href="${escapeHtml(profiles[key])}" target="_blank" rel="noopener">${label}</a>`)
    .join("");
  if (links) return `<div class="social-links">${links}</div>`;
  if (contact.social_error) return `<span class="muted" title="${escapeHtml(contact.social_error)}">未找到</span>`;
  return `<span class="muted">待富化</span>`;
}

function isHttpUrl(value) {
  return /^https?:\/\//i.test(String(value || ""));
}

function displayEmail(contact) {
  if (!contact.email || String(contact.email).includes("*")) return "待富化";
  return contact.email;
}

function emailMeta(contact) {
  const parts = [contact.email_status || "unknown"];
  if (contact.email_source) parts.push(sourceLabel(contact.email_source));
  if (contact.email_confidence !== null && contact.email_confidence !== undefined) parts.push(`${contact.email_confidence}%`);
  return parts.join(" · ");
}

function sourceLabel(source) {
  return {
    existing: "已有",
    public_website: "官网",
    ninjapear: "NinjaPear",
    prospeo: "Prospeo",
    hunter: "Hunter",
    linkedin_public_search: "LinkedIn 公网搜索",
    linkedin_public_search_guess: "LinkedIn 推断",
    "linkedin_public_search+hunter_verify": "LinkedIn+Hunter 验证",
    "linkedin_public_search+prospeo": "LinkedIn+Prospeo",
    "pattern_guess+hunter_verify": "推断+验证",
  }[source] || source;
}

function statusLabel(status) {
  return {
    new: "新线索",
    enriched: "已富化",
    queued: "待发送",
    sent_1: "已发第 1 封",
    sent_2: "已发第 2 封",
    sent_3: "已发第 3 封",
    replied: "已回复",
    bounced: "已退信",
    unsubscribed: "已退订",
  }[status] || status;
}

function lifecycleLabel(stage) {
  return Object.fromEntries(lifecycleStages)[stage] || stage || "陌生线索";
}

function dispositionLabel(disposition) {
  return {
    active: "推进中",
    waiting: "等待",
    abandoned: "已放弃",
    won: "已签约",
    lost: "流失",
  }[disposition] || disposition || "推进中";
}

function renderLifecycleCell(contact) {
  return `
    <div class="lifecycle-cell">
      <span class="stage-pill">${escapeHtml(lifecycleLabel(contact.lifecycle_stage))}</span>
      <div class="muted">${escapeHtml(dispositionLabel(contact.disposition))}</div>
      ${contact.next_action_at ? `<div class="muted">下次：${formatDate(contact.next_action_at)}</div>` : ""}
    </div>
  `;
}

function renderProfileSummary(contact) {
  const insights = contact.profile_insights || {};
  if (!contact.profile_summary && !Object.keys(insights).length) {
    return `<div class="profile-summary muted">待生成画像</div>`;
  }
  const score = Number(insights.icp_fit_score ?? 0);
  const intent = intentLabel(insights.intent_level);
  const nextAction = insights.next_action || contact.profile_summary || "";
  const tags = [
    ...(insights.interests || []).slice(0, 2),
    ...(insights.pain_points || []).slice(0, 1),
  ].map((item) => `<span>${escapeHtml(item)}</span>`).join("");
  return `
    <div class="profile-insights" title="${escapeHtml(contact.profile_summary || "")}">
      <div class="fit-line">
        <b>${score || "--"}</b>
        <span>${escapeHtml(intent)}</span>
      </div>
      <strong>${escapeHtml(insights.persona || contact.profile_summary || "客户画像")}</strong>
      <p>${escapeHtml(nextAction || "暂无下一步建议")}</p>
      ${tags ? `<div class="insight-tags">${tags}</div>` : ""}
    </div>
  `;
}

function intentLabel(level) {
  return {
    high: "高意向",
    medium: "中意向",
    low: "低意向",
    unknown: "待判断",
  }[level] || "待判断";
}

function renderRowActions(contact) {
  return `
    <div class="row-actions">
      <button data-life-action="enrich-email" data-id="${contact.id}">邮箱</button>
      <button data-life-action="enrich-social" data-id="${contact.id}">社媒</button>
      <button data-life-action="queue-one" data-id="${contact.id}">入队</button>
      <button data-life-action="send-one" data-id="${contact.id}">发送</button>
      <button data-life-action="next" data-id="${contact.id}">推进</button>
      <button data-life-action="wait" data-id="${contact.id}">等待</button>
      <button data-life-action="abandon" data-id="${contact.id}">放弃</button>
      <button data-life-action="profile" data-id="${contact.id}">画像</button>
      <button data-life-action="detail" data-id="${contact.id}">详情</button>
    </div>
  `;
}

function renderEmailFeedback(contact) {
  const items = [];
  if (Number(contact.sent_count || 0) > 0) items.push(["sent", `已发送 ${contact.sent_count}`]);
  if (Number(contact.opened_count || 0) > 0) items.push(["opened", `已打开 ${contact.opened_count}`]);
  if (Number(contact.clicked_count || 0) > 0) items.push(["clicked", `已点击 ${contact.clicked_count}`]);
  if (Number(contact.replied_count || 0) > 0) items.push(["replied", "已回复"]);
  if (Number(contact.bounced_count || 0) > 0) items.push(["bounced", "已退信"]);
  if (Number(contact.unsubscribed_count || 0) > 0) items.push(["unsubscribed", "已退订"]);
  if (!items.length) return `<span class="muted">暂无反馈</span>`;
  const chips = items
    .map(([type, label]) => `<span class="event-chip ${type}">${escapeHtml(label)}</span>`)
    .join("");
  const lastEvent = contact.last_event_type ? `<div class="muted">最近：${escapeHtml(eventLabel(contact.last_event_type))} ${formatDate(contact.last_event_at)}</div>` : "";
  return `<div class="event-list">${chips}${lastEvent}</div>`;
}

function eventLabel(type) {
  return {
    sent: "已发送",
    opened: "已打开",
    clicked: "已点击",
    replied: "已回复",
    bounced: "已退信",
    unsubscribed: "已退订",
  }[type] || type;
}

function formatDate(value) {
  if (!value) return "";
  return new Date(value).toLocaleString();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function normalizeCompanyWebsite(value) {
  try {
    const raw = String(value || "").trim();
    if (!raw) return "";
    const url = new URL(raw.includes("://") ? raw : `https://${raw}`);
    let host = url.hostname.toLowerCase();
    if (host.startsWith("www.")) host = host.slice(4);
    const parts = host.split(".").filter(Boolean);
    if (parts.length > 2) host = parts.slice(-2).join(".");
    return host;
  } catch {
    return String(value || "").trim().replace(/^https?:\/\//, "").replace(/^www\./, "").split("/")[0];
  }
}

async function runAction(action) {
  showNotice("正在执行，请稍等...");
  const data = await api(`/api/${action}`, { method: "POST", body: JSON.stringify({ limit: 100 }) });
  updateUsage(data.usage);
  showNotice(`完成：${JSON.stringify(data)}`);
  await refresh();
}

document.querySelectorAll("[data-action]").forEach((button) => {
  button.addEventListener("click", async () => {
    try {
      await runAction(button.dataset.action);
    } catch (error) {
      showNotice(error.message, "error");
    }
  });
});

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    document.querySelector(`#tab-${button.dataset.tab}`).classList.add("active");
  });
});

document.querySelector("#refresh-button").addEventListener("click", refresh);

if (!window.SALESBOT_REACT_AUTH) {
  loginForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    loginError.textContent = "";
    try {
      const password = document.querySelector("#login-password").value;
      const session = await api("/api/login", {
        method: "POST",
        body: JSON.stringify({
          username: document.querySelector("#login-username").value.trim(),
          password,
        }),
      });
      state.user = session.user;
      state.usage = session.usage;
      document.querySelector("#current-password").value = password;
      renderAccount();
      if (state.user.must_change_password) {
        showPasswordChange();
        return;
      }
      hideLogin();
      await refresh();
    } catch (error) {
      loginError.textContent = error.message;
    }
  });

  passwordChangeForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    passwordChangeError.textContent = "";
    try {
      const currentPassword = document.querySelector("#current-password").value;
      const newPassword = document.querySelector("#new-password").value;
      const confirmPassword = document.querySelector("#confirm-password").value;
      if (newPassword.length < 12) throw new Error("新密码至少 12 位");
      if (newPassword !== confirmPassword) throw new Error("两次输入的新密码不一致");
      const result = await api("/api/change-password", {
        method: "POST",
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });
      state.user = result.user;
      document.querySelector("#login-password").value = "";
      document.querySelector("#current-password").value = "";
      document.querySelector("#new-password").value = "";
      document.querySelector("#confirm-password").value = "";
      renderAccount();
      hideLogin();
      showNotice("密码已更新");
      await refresh();
    } catch (error) {
      passwordChangeError.textContent = error.message;
    }
  });
}

logoutButton.addEventListener("click", async () => {
  await fetch("/api/logout");
  state.user = null;
  state.usage = null;
  renderAccount();
  window.dispatchEvent(new CustomEvent("salesbot:logout"));
  showLogin();
});

document.querySelector("#export-button").addEventListener("click", () => {
  const params = new URLSearchParams();
  if (state.status) params.set("status", state.status);
  window.location.href = `/api/export.csv?${params.toString()}`;
});

document.querySelector("#status-filter").addEventListener("change", (event) => {
  state.status = event.target.value;
  refresh();
});

document.querySelector("#contact-filter").addEventListener("change", (event) => {
  state.filter = event.target.value;
  refresh();
});

document.querySelector("#search-input").addEventListener("input", (event) => {
  state.search = event.target.value;
  window.clearTimeout(window.searchTimer);
  window.searchTimer = window.setTimeout(refresh, 250);
});

if (!window.SALESBOT_REACT_ADMIN) {
  document.querySelector("#admin-create-user")?.addEventListener("click", async () => {
    try {
      const payload = {
        username: document.querySelector("#admin-new-username").value.trim(),
        display_name: document.querySelector("#admin-new-display").value.trim(),
        password: document.querySelector("#admin-new-password").value,
        role: "sales",
        daily_source_limit: Number(document.querySelector("#admin-new-source-limit").value || 100),
        daily_send_limit: Number(document.querySelector("#admin-new-send-limit").value || 100),
      };
      if (!payload.username || !payload.password) throw new Error("账号和密码必填");
      await api("/api/admin/users", { method: "POST", body: JSON.stringify(payload) });
      showNotice("销售账号已创建");
      document.querySelector("#admin-new-password").value = "";
      await refreshAdminConsole();
      await refreshOpsReport();
    } catch (error) {
      showNotice(error.message, "error");
    }
  });

  adminUsersTable?.addEventListener("click", async (event) => {
    const save = event.target.closest("[data-admin-user-save]");
    const toggle = event.target.closest("[data-admin-user-toggle]");
    const reset = event.target.closest("[data-admin-user-reset]");
    if (!save && !toggle && !reset) return;
    try {
      const userId = Number((save || toggle || reset).dataset.adminUserSave || (save || toggle || reset).dataset.adminUserToggle || (save || toggle || reset).dataset.adminUserReset);
      const payload = { user_id: userId };
      if (save) {
        payload.daily_source_limit = Number(document.querySelector(`[data-user-source="${userId}"]`).value || 100);
        payload.daily_send_limit = Number(document.querySelector(`[data-user-send="${userId}"]`).value || 100);
      }
      if (toggle) {
        payload.active = toggle.dataset.active === "true";
      }
      if (reset) {
        const password = window.prompt("输入新密码，至少 8 位");
        if (!password) return;
        if (password.length < 8) throw new Error("密码至少 8 位");
        payload.password = password;
      }
      await api("/api/admin/user", { method: "POST", body: JSON.stringify(payload) });
      showNotice("用户已更新");
      await refreshAdminConsole();
      await refreshOpsReport();
    } catch (error) {
      showNotice(error.message, "error");
    }
  });

  adminSendersTable?.addEventListener("click", async (event) => {
    const save = event.target.closest("[data-admin-sender-save]");
    const toggle = event.target.closest("[data-admin-sender-toggle]");
    if (!save && !toggle) return;
    try {
      const senderId = Number((save || toggle).dataset.adminSenderSave || (save || toggle).dataset.adminSenderToggle);
      const payload = { sender_id: senderId };
      if (save) {
        payload.daily_limit = Number(document.querySelector(`[data-sender-limit="${senderId}"]`).value || 100);
        payload.warmup_stage = document.querySelector(`[data-sender-warmup="${senderId}"]`).value;
      }
      if (toggle) {
        payload.active = toggle.dataset.active === "true";
      }
      await api("/api/admin/sender", { method: "POST", body: JSON.stringify(payload) });
      showNotice("发件账号已更新");
      await refreshAdminConsole();
    } catch (error) {
      showNotice(error.message, "error");
    }
  });
}

document.querySelector("#mark-button").addEventListener("click", async () => {
  try {
    const contactId = document.querySelector("#mark-id").value;
    const status = document.querySelector("#mark-status").value;
    await api("/api/mark", { method: "POST", body: JSON.stringify({ contact_id: Number(contactId), status }) });
    showNotice("状态已更新");
    await refresh();
  } catch (error) {
    showNotice(error.message, "error");
  }
});

document.querySelector("#blacklist-button").addEventListener("click", async () => {
  try {
    await api("/api/blacklist", {
      method: "POST",
      body: JSON.stringify({
        email: document.querySelector("#blacklist-email").value || null,
        domain: document.querySelector("#blacklist-domain").value || null,
        reason: "dashboard",
      }),
    });
    showNotice("黑名单已更新");
    await refresh();
  } catch (error) {
    showNotice(error.message, "error");
  }
});

document.querySelector("#add-lead-button").addEventListener("click", async () => {
  try {
    const payload = {
      linkedin_url: document.querySelector("#lead-linkedin").value,
      first_name: document.querySelector("#lead-first").value || null,
      last_name: document.querySelector("#lead-last").value || null,
      email: document.querySelector("#lead-email").value || null,
      email_status: document.querySelector("#lead-email").value ? "valid" : "unknown",
      status: document.querySelector("#lead-email").value ? "enriched" : "new",
      job_title: document.querySelector("#lead-title").value || null,
      company_name: document.querySelector("#lead-company").value || null,
      company_domain: document.querySelector("#lead-domain").value || null,
      source: "manual_dashboard",
    };
    if (!payload.linkedin_url) throw new Error("LinkedIn URL 必填");
    const result = await api("/api/contacts", { method: "POST", body: JSON.stringify(payload) });
    showNotice(`新增完成：${JSON.stringify(result)}`);
    await refresh();
  } catch (error) {
    showNotice(error.message, "error");
  }
});

document.querySelector("#csv-import-button").addEventListener("click", async () => {
  try {
    const file = document.querySelector("#csv-file").files[0];
    if (!file) throw new Error("请选择 CSV 文件");
    const csv = await file.text();
    const result = await api("/api/import/csv", {
      method: "POST",
      body: JSON.stringify({ csv, source: `csv:${file.name}` }),
    });
    showNotice(`CSV 导入完成：解析 ${result.parsed} 条，新增 ${result.inserted} 条，重复 ${result.skipped} 条`);
    await refresh();
  } catch (error) {
    showNotice(error.message, "error");
  }
});

document.querySelector("#source-button").addEventListener("click", async () => {
  try {
    const payload = {
      company_website: normalizeCompanyWebsite(document.querySelector("#source-company").value),
      role: document.querySelector("#source-role").value,
      industry: document.querySelector("#source-industry").value,
      location: document.querySelector("#source-location").value,
      limit: Number(document.querySelector("#source-limit").value || 25),
    };
    if (!payload.role) throw new Error("Role 必填");
    showNotice("正在调用 Prospeo 自动获客，Limit 越大等待越久...");
    const result = await api("/api/source", { method: "POST", body: JSON.stringify(payload) });
    updateUsage(result.usage);
    const outcome = result.result || [0, 0];
    showNotice(`自动获客完成：新增 ${outcome[0]} 条，重复 ${outcome[1]} 条`);
    await refresh();
  } catch (error) {
    showNotice(error.message, "error");
  }
});

document.querySelector("#linkedin-search-button").addEventListener("click", async () => {
  try {
    const payload = {
      role: document.querySelector("#linkedin-role").value.trim(),
      industry: document.querySelector("#linkedin-industry").value.trim(),
      location: document.querySelector("#linkedin-location").value.trim(),
      company_keyword: document.querySelector("#linkedin-company").value.trim(),
      limit: Number(document.querySelector("#linkedin-limit").value || 10),
      auto_domain_lookup: document.querySelector("#linkedin-auto-domain").checked,
      auto_generate_email_candidates: document.querySelector("#linkedin-auto-candidates").checked,
      high_confidence_verify: document.querySelector("#linkedin-high-verify").checked,
    };
    if (!payload.role && !payload.industry && !payload.company_keyword) {
      throw new Error("至少填写职位、行业或公司关键词");
    }
    showNotice("正在通过 Google 公开索引搜索 LinkedIn 个人主页...");
    const response = await api("/api/source/linkedin-public-search", { method: "POST", body: JSON.stringify(payload) });
    updateUsage(response.usage);
    state.linkedinTaskId = response.result.task_id;
    showNotice(`LinkedIn 公网搜索完成：解析 ${response.result.results} 条，入库 ${response.result.promoted} 条，跳过 ${response.result.skipped} 条`);
    await refresh();
    await loadLinkedInSearchResults(state.linkedinTaskId);
  } catch (error) {
    showNotice(error.message, "error");
  }
});

document.querySelector("#linkedin-refresh-button").addEventListener("click", async () => {
  try {
    await refreshLinkedInSearchTasks();
    if (state.linkedinTaskId) await loadLinkedInSearchResults(state.linkedinTaskId);
    showNotice("LinkedIn 搜索结果已刷新");
  } catch (error) {
    showNotice(error.message, "error");
  }
});

linkedinSearchOutput.addEventListener("click", async (event) => {
  const taskButton = event.target.closest("[data-search-task]");
  const promoteButton = event.target.closest("[data-promote-result]");
  try {
    if (taskButton) {
      state.linkedinTaskId = Number(taskButton.dataset.searchTask);
      await loadLinkedInSearchResults(state.linkedinTaskId);
      return;
    }
    if (promoteButton) {
      const resultId = Number(promoteButton.dataset.promoteResult);
      const result = await api("/api/search-results/promote", { method: "POST", body: JSON.stringify({ result_id: resultId }) });
      showNotice(result.contact_id ? `已入库联系人 #${result.contact_id}` : "已处理，可能是重复客户");
      await refresh();
      if (state.linkedinTaskId) await loadLinkedInSearchResults(state.linkedinTaskId);
    }
  } catch (error) {
    showNotice(error.message, "error");
  }
});

contactsBody.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-life-action]");
  if (!button) return;
  const contactId = Number(button.dataset.id);
  const action = button.dataset.lifeAction;
  try {
    if (action === "enrich-email") {
      showNotice("正在富化当前客户邮箱...");
      const result = await api("/api/enrich-one", { method: "POST", body: JSON.stringify({ contact_id: contactId }) });
      const fields = result.fields || {};
      if (fields.email_status === "valid") {
        showNotice(`已找到邮箱：${fields.email}`);
      } else {
        showNotice("没有找到已验证邮箱，稍后可以换数据源或补充更多客户信息再试", "error");
      }
    } else if (action === "enrich-social") {
      showNotice("正在富化当前客户社媒...");
      const result = await api("/api/social-enrich-one", { method: "POST", body: JSON.stringify({ contact_id: contactId }) });
      showNotice(result.ok ? "社媒资料已更新" : "没有找到可用社媒主页", result.ok ? "" : "error");
    } else if (action === "queue-one") {
      showNotice("正在把当前客户加入发送队列...");
      const result = await api("/api/queue-one", { method: "POST", body: JSON.stringify({ contact_id: contactId }) });
      showNotice(result.queued ? "已加入发送队列" : "未能入队：需要先有有效邮箱，且客户不能在黑名单里", result.queued ? "" : "error");
    } else if (action === "send-one") {
      showNotice("正在发送当前客户的下一封邮件...");
      const result = await api("/api/send-one", { method: "POST", body: JSON.stringify({ contact_id: contactId }) });
      updateUsage(result.usage);
      showNotice(result.sent ? "邮件已发送" : "未发送：需要先入队、满足发送间隔，并且未超过每日发送上限", result.sent ? "" : "error");
    } else if (action === "profile") {
      showNotice("正在生成客户画像...");
      const result = await api("/api/profile-agent", { method: "POST", body: JSON.stringify({ contact_id: contactId }) });
      const insights = result.insights || {};
      showNotice(`画像已更新：拟合度 ${insights.icp_fit_score ?? "--"}，下一步：${insights.next_action || insights.summary || "已生成"}`);
    } else if (action === "detail") {
      await loadCustomerWorkspace(contactId);
      showNotice("客户详情已打开");
    } else {
      const payload = lifecyclePayload(action, contactId);
      await api("/api/lifecycle", { method: "POST", body: JSON.stringify(payload) });
      showNotice("生命周期状态已更新");
    }
    await refresh();
  } catch (error) {
    showNotice(error.message, "error");
  }
});

document.querySelector("#save-activity-button").addEventListener("click", async () => {
  try {
    if (!window.selectedContactId) throw new Error("请先选择客户");
    const payload = {
      contact_id: window.selectedContactId,
      lifecycle_stage: document.querySelector("#activity-stage").value,
      activity_type: document.querySelector("#activity-type").value,
      content: document.querySelector("#activity-content").value.trim(),
      created_by: "dashboard",
    };
    if (!payload.content) throw new Error("请填写阶段记录");
    const activity = await api("/api/lifecycle-activity", { method: "POST", body: JSON.stringify(payload) });
    showNotice("阶段记录已保存");
    await loadCustomerWorkspace(window.selectedContactId);
    await refresh();
    return activity;
  } catch (error) {
    showNotice(error.message, "error");
  }
});

document.querySelector("#stage-agent-button").addEventListener("click", async () => {
  try {
    if (!window.selectedContactId) throw new Error("请先选择客户");
    const payload = {
      contact_id: window.selectedContactId,
      lifecycle_stage: document.querySelector("#activity-stage").value,
      activity_type: document.querySelector("#activity-type").value,
      content: document.querySelector("#activity-content").value.trim(),
    };
    showNotice("AI 正在分析当前阶段...");
    const result = await api("/api/stage-agent", { method: "POST", body: JSON.stringify(payload) });
    stageAnalysis.innerHTML = renderStageAnalysis(result.analysis);
    showNotice("AI 阶段分析已生成");
  } catch (error) {
    showNotice(error.message, "error");
  }
});

activityList.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-analyze-activity]");
  if (!button) return;
  try {
    showNotice("AI 正在重新分析记录...");
    await api("/api/stage-agent", {
      method: "POST",
      body: JSON.stringify({
        contact_id: window.selectedContactId,
        activity_id: Number(button.dataset.analyzeActivity),
      }),
    });
    await loadCustomerWorkspace(window.selectedContactId);
    showNotice("记录分析已更新");
  } catch (error) {
    showNotice(error.message, "error");
  }
});

workspaceContent.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-adopt-email]");
  if (!button) return;
  try {
    const contactId = Number(button.dataset.contactId);
    const email = button.dataset.adoptEmail;
    await api("/api/email-candidates/adopt", { method: "POST", body: JSON.stringify({ contact_id: contactId, email }) });
    showNotice(`已采用候选邮箱：${email}`);
    await loadCustomerWorkspace(contactId);
    await refresh();
  } catch (error) {
    showNotice(error.message, "error");
  }
});

document.querySelector("#draft-email-button").addEventListener("click", async () => {
  try {
    if (!window.selectedContactId) throw new Error("请先选择客户");
    showNotice("正在生成邮件草稿...");
    const result = await api("/api/email-draft", {
      method: "POST",
      body: JSON.stringify({
        contact_id: window.selectedContactId,
        mode: document.querySelector("#email-mode").value,
        subject: document.querySelector("#email-subject").value,
        body: document.querySelector("#email-body").value,
      }),
    });
    document.querySelector("#email-subject").value = result.subject || "";
    document.querySelector("#email-body").value = result.body || "";
    showNotice("邮件草稿已生成，请检查后再发送");
  } catch (error) {
    showNotice(error.message, "error");
  }
});

document.querySelector("#send-custom-email-button").addEventListener("click", async () => {
  try {
    if (!window.selectedContactId) throw new Error("请先选择客户");
    const subject = document.querySelector("#email-subject").value.trim();
    const body = document.querySelector("#email-body").value.trim();
    if (!subject || !body) throw new Error("请先填写主题和正文");
    if (!window.confirm("确认发送给当前客户？dry_run=false 时会真实发出。")) return;
    showNotice("正在发送邮件...");
    const result = await api("/api/send-custom", {
      method: "POST",
      body: JSON.stringify({
        contact_id: window.selectedContactId,
        mode: document.querySelector("#email-mode").value,
        subject,
        body,
      }),
    });
    showNotice(`邮件已发送：第 ${result.step} 封`);
    await loadCustomerWorkspace(window.selectedContactId);
    await refresh();
  } catch (error) {
    showNotice(error.message, "error");
  }
});

async function refreshLinkedInSearchTasks() {
  if (!linkedinSearchOutput || !state.user) return;
  const response = await api("/api/search-tasks");
  const tasks = response.tasks || [];
  if (!tasks.length) {
    linkedinSearchOutput.innerHTML = `
      <div class="empty-state compact">
        <strong>还没有 LinkedIn 公网搜索任务</strong>
        <p>填写上方条件后开始搜索，结果会先进入候选池和客户列表，不会自动发邮件。</p>
      </div>
    `;
    return;
  }
  if (!state.linkedinTaskId) state.linkedinTaskId = tasks[0].id;
  linkedinSearchOutput.innerHTML = `
    <div class="search-task-strip">
      ${tasks.slice(0, 8).map((task) => `
        <button class="${Number(task.id) === Number(state.linkedinTaskId) ? "active" : ""}" data-search-task="${task.id}">
          <strong>#${task.id} ${escapeHtml(task.status)}</strong>
          <span>${escapeHtml(searchTaskTitle(task))}</span>
          <small>结果 ${task.result_count || 0} · 入库 ${task.promoted_count || 0}</small>
        </button>
      `).join("")}
    </div>
    <div id="linkedin-result-panel" class="search-result-panel"></div>
  `;
  await loadLinkedInSearchResults(state.linkedinTaskId);
}

async function loadLinkedInSearchResults(taskId) {
  if (!taskId) return;
  const panel = document.querySelector("#linkedin-result-panel");
  if (!panel) return;
  const response = await api(`/api/search-results?task_id=${encodeURIComponent(taskId)}`);
  const rows = response.results || [];
  panel.innerHTML = renderLinkedInSearchResults(rows);
}

function renderLinkedInSearchResults(rows) {
  if (!rows.length) {
    return `<div class="empty-state compact"><strong>该任务暂无结果</strong><p>如果 Google CSE 没有返回内容，可以放宽职位或地区关键词。</p></div>`;
  }
  return `
    <div class="linkedin-results">
      ${rows.map((row) => `
        <article class="linkedin-result ${escapeHtml(row.status || "")}">
          <header>
            <div>
              <strong>${escapeHtml(fullName(row) || row.raw_title || "未解析姓名")}</strong>
              <span>${escapeHtml(row.job_title || "职位待确认")} · ${escapeHtml(row.company_name || "公司待确认")}</span>
            </div>
            <b>${Number(row.lead_score || 0)}</b>
          </header>
          <p>${escapeHtml(row.raw_snippet || "")}</p>
          <div class="result-meta">
            <span>${escapeHtml(row.company_domain || "域名待补")}</span>
            <span>${escapeHtml(row.location || "地区待确认")}</span>
            <span>${escapeHtml(searchResultStatus(row.status))}</span>
            ${row.failure_reason ? `<span class="danger-text">${escapeHtml(row.failure_reason)}</span>` : ""}
          </div>
          <div class="result-candidates">
            ${renderInlineEmailCandidates(row.email_candidates || [])}
          </div>
          <footer>
            <a href="${escapeHtml(row.linkedin_url || row.raw_url || "#")}" target="_blank" rel="noopener">打开 LinkedIn</a>
            ${row.promoted_contact_id ? `<span>已入库 #${row.promoted_contact_id}</span>` : `<button data-promote-result="${row.id}">入库</button>`}
          </footer>
        </article>
      `).join("")}
    </div>
  `;
}

function renderInlineEmailCandidates(candidates) {
  if (!Array.isArray(candidates) || !candidates.length) return `<span class="muted">暂无邮箱候选</span>`;
  return candidates.slice(0, 4).map((item) => `
    <span class="candidate-chip ${escapeHtml(item.category || "")}">
      ${escapeHtml(item.email || "")}
      <small>${Number(item.confidence || 0)}%</small>
    </span>
  `).join("");
}

function searchTaskTitle(task) {
  const criteria = task.criteria || {};
  return [criteria.role || criteria.title, criteria.industry, criteria.location, criteria.company_keyword].filter(Boolean).join(" / ") || "公开搜索";
}

function searchResultStatus(status) {
  return {
    candidate: "候选",
    low_score: "低分跳过",
    promoted: "已入库",
    duplicate: "重复",
    failed: "失败",
  }[status] || status || "未知";
}

function lifecyclePayload(action, contactId) {
  const contact = findContactInTable(contactId);
  if (action === "next") {
    return {
      contact_id: contactId,
      lifecycle_stage: nextLifecycleStage(contact?.lifecycle_stage),
      disposition: "active",
      notes: "dashboard: move to next lifecycle stage",
    };
  }
  if (action === "wait") {
    const next = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString();
    return {
      contact_id: contactId,
      lifecycle_stage: contact?.lifecycle_stage || "waiting_pool",
      disposition: "waiting",
      next_action_at: next,
      notes: "dashboard: move to waiting follow-up pool",
    };
  }
  return {
    contact_id: contactId,
    lifecycle_stage: "abandoned",
    disposition: "abandoned",
    lost_reason: "dashboard: manually abandoned",
    notes: "dashboard: abandon customer lifecycle",
  };
}

async function loadCustomerWorkspace(contactId) {
  const detail = await api(`/api/contact-detail?contact_id=${encodeURIComponent(contactId)}`);
  if (!detail.contact) throw new Error("客户不存在");
  window.selectedContactId = contactId;
  window.selectedContactDetail = detail;
  workspaceEmpty.classList.add("hidden");
  workspaceContent.classList.remove("hidden");
  renderWorkspaceProfile(detail.contact);
  renderActivityList(detail.activities || []);
  stageAnalysis.innerHTML = "";
  document.querySelector("#activity-stage").value = detail.contact.lifecycle_stage || "lead";
  document.querySelector("#email-mode").value = "ai";
  document.querySelector("#email-subject").value = `Quick question about ${detail.contact.company_name || "your business"}`;
  document.querySelector("#email-body").value = "";
  document.querySelector("#activity-content").value = "";
  document.querySelector("#customer-workspace").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderWorkspaceProfile(contact) {
  const insights = contact.profile_insights || {};
  workspaceProfile.innerHTML = `
    <div>
      <strong>${escapeHtml(fullName(contact))}</strong>
      <span>${escapeHtml(contact.job_title || "")} · ${escapeHtml(contact.company_name || "")}</span>
    </div>
    <div>
      <b>${escapeHtml(lifecycleLabel(contact.lifecycle_stage))}</b>
      <span>${escapeHtml(dispositionLabel(contact.disposition))}</span>
    </div>
    <div>
      <b>${insights.icp_fit_score ?? "--"}</b>
      <span>拟合度 / ${escapeHtml(intentLabel(insights.intent_level))}</span>
    </div>
    <p>${escapeHtml(contact.profile_summary || "还没有客户画像，点击列表里的“画像”生成。")}</p>
    ${renderEmailCandidates(contact)}
  `;
}

function renderEmailCandidates(contact) {
  const candidates = Array.isArray(contact.email_candidates) ? contact.email_candidates : [];
  if (!candidates.length) {
    return `
      <section class="email-candidates empty">
        <header><strong>邮箱候选</strong><span>暂无候选</span></header>
      </section>
    `;
  }
  const rows = candidates.slice(0, 6).map((item) => `
    <div class="candidate-row ${escapeHtml(item.category || "")}">
      <strong>${escapeHtml(item.email || "")}</strong>
      <span>${escapeHtml(sourceLabel(item.source || ""))}</span>
      <span>${escapeHtml(candidateCategoryLabel(item.category))}</span>
      <span>${escapeHtml(item.status || "unknown")}</span>
      <b>${Number(item.confidence || 0)}%</b>
      ${item.category === "personal_work" ? `<button data-adopt-email="${escapeHtml(item.email || "")}" data-contact-id="${contact.id}">采用</button>` : ""}
    </div>
  `).join("");
  return `
    <section class="email-candidates">
      <header><strong>邮箱候选</strong><span>只把个人 valid 邮箱作为正式发信邮箱</span></header>
      ${rows}
    </section>
  `;
}

function candidateCategoryLabel(category) {
  return {
    personal_work: "个人工作邮箱",
    personal_free: "个人邮箱",
    company_generic: "公司通用邮箱",
  }[category] || "未分类";
}

function renderActivityList(activities) {
  if (!activities.length) {
    activityList.innerHTML = `<div class="empty-activity">还没有阶段记录。</div>`;
    return;
  }
  activityList.innerHTML = activities.map((item) => `
    <article class="activity-card">
      <header>
        <strong>${escapeHtml(lifecycleLabel(item.lifecycle_stage))} / ${escapeHtml(activityTypeLabel(item.activity_type))}</strong>
        <span>${formatDate(item.created_at)}</span>
      </header>
      <p>${escapeHtml(item.content)}</p>
      ${renderStageAnalysis(item.ai_analysis)}
      <button data-analyze-activity="${item.id}">重新分析</button>
    </article>
  `).join("");
}

function activityTypeLabel(type) {
  return {
    reply: "回复内容",
    research: "客户资料/背景调研",
    meeting_note: "会议纪要",
    business_plan: "商业计划",
    trial_order: "试订单",
    agreement_review: "代理协议风险",
    store_plan: "门店创建资料",
    note: "普通备注",
  }[type] || type;
}

function renderStageAnalysis(analysis) {
  if (!analysis || !Object.keys(analysis).length) return "";
  const list = (label, items) => items?.length ? `<div><b>${label}</b>${items.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>` : "";
  return `
    <section class="analysis-card">
      <strong>${escapeHtml(analysis.summary || "AI 阶段分析")}</strong>
      ${list("下一步", analysis.next_steps)}
      ${list("缺失资料", analysis.missing_info)}
      ${list("风险", analysis.risks)}
      ${list("准备材料", analysis.materials_to_prepare)}
    </section>
  `;
}

function findContactInTable(contactId) {
  return window.latestContacts?.find((contact) => Number(contact.id) === Number(contactId));
}

function nextLifecycleStage(stage) {
  const order = ["lead", "replied", "conversation", "meeting", "business_plan", "store_visit", "trial_order", "agency_agreement", "hq_visit", "signed", "maintenance"];
  const index = order.indexOf(stage || "lead");
  return order[Math.min(index + 1, order.length - 1)];
}

loadSession();
