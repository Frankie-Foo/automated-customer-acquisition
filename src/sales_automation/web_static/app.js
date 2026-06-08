const state = {
  status: "",
  search: "",
  user: null,
  usage: null,
};

const statusOrder = ["new", "enriched", "queued", "sent_1", "sent_2", "sent_3", "replied", "bounced", "unsubscribed"];

const notice = document.querySelector("#notice");
const metrics = document.querySelector("#metrics");
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
const accountName = document.querySelector("#account-name");
const quotaStatus = document.querySelector("#quota-status");
const logoutButton = document.querySelector("#logout-button");

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
    throw new Error("请先登录");
  }
  if (!data.ok) throw new Error(data.error || "请求失败");
  return data.data;
}

async function loadSession() {
  try {
    const session = await api("/api/me");
    state.user = session.user;
    state.usage = session.usage;
    renderAccount();
    hideLogin();
    await refresh();
  } catch (error) {
    showLogin();
  }
}

function showLogin() {
  loginScreen.classList.remove("hidden");
}

function hideLogin() {
  loginScreen.classList.add("hidden");
  loginError.textContent = "";
}

function renderAccount() {
  if (!state.user) {
    accountName.textContent = "未登录";
    quotaStatus.textContent = "今日配额 --";
    return;
  }
  const usage = state.usage || {};
  accountName.textContent = state.user.display_name || state.user.username;
  quotaStatus.textContent = `获客 ${usage.source_count || 0}/${state.user.daily_source_limit} · 发信 ${usage.send_count || 0}/${state.user.daily_send_limit}`;
}

function updateUsage(usage) {
  if (!usage) return;
  state.usage = usage;
  renderAccount();
}

async function refresh() {
  try {
    const [summary, contacts, lifecycle] = await Promise.all([
      api("/api/summary"),
      api(`/api/contacts?status=${encodeURIComponent(state.status)}&search=${encodeURIComponent(state.search)}&limit=100`),
      api("/api/lifecycle"),
    ]);
    hideNotice();
    renderMetrics(summary);
    const rows = contacts.contacts || [];
    window.latestContacts = rows;
    renderFollowups(rows);
    renderLifecycle(lifecycle, rows);
    renderContacts(rows);
    refreshReadiness();
  } catch (error) {
    renderMetrics({ total_contacts: 0, sent_today: 0, statuses: {}, events_7d: {} });
    renderFollowups([]);
    renderLifecycle({ stages: {}, outreach: {}, actions: [] }, []);
    renderContacts([]);
    showNotice(`数据库还不可用：${error.message}。先确认 .env/config.yaml，然后点“初始化/迁移数据库”。`, "error");
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

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  loginError.textContent = "";
  try {
    const session = await api("/api/login", {
      method: "POST",
      body: JSON.stringify({
        username: document.querySelector("#login-username").value.trim(),
        password: document.querySelector("#login-password").value,
      }),
    });
    state.user = session.user;
    state.usage = session.usage;
    renderAccount();
    hideLogin();
    await refresh();
  } catch (error) {
    loginError.textContent = error.message;
  }
});

logoutButton.addEventListener("click", async () => {
  await fetch("/api/logout");
  state.user = null;
  state.usage = null;
  renderAccount();
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

document.querySelector("#search-input").addEventListener("input", (event) => {
  state.search = event.target.value;
  window.clearTimeout(window.searchTimer);
  window.searchTimer = window.setTimeout(refresh, 250);
});

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
