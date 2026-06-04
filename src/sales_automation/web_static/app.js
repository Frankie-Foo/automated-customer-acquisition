const state = {
  status: "",
  search: "",
};

const statusOrder = ["new", "enriched", "queued", "sent_1", "sent_2", "sent_3", "replied", "bounced", "unsubscribed"];

const notice = document.querySelector("#notice");
const metrics = document.querySelector("#metrics");
const followupGrid = document.querySelector("#followup-grid");
const contactsBody = document.querySelector("#contacts-body");
const readinessNode = document.querySelector("#readiness");
const readyPill = document.querySelector("#ready-pill");

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
  if (!data.ok) throw new Error(data.error || "请求失败");
  return data.data;
}

async function refresh() {
  try {
    const [summary, contacts] = await Promise.all([
      api("/api/summary"),
      api(`/api/contacts?status=${encodeURIComponent(state.status)}&search=${encodeURIComponent(state.search)}&limit=100`),
    ]);
    hideNotice();
    renderMetrics(summary);
    const rows = contacts.contacts || [];
    renderFollowups(rows);
    renderContacts(rows);
    refreshReadiness();
  } catch (error) {
    renderMetrics({ total_contacts: 0, sent_today: 0, statuses: {}, events_7d: {} });
    renderFollowups([]);
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
        <td colspan="9">
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
      </td>
      <td>
        <strong>${escapeHtml(contact.company_name || "")}</strong>
        <div class="muted">${escapeHtml(contact.company_domain || "")}</div>
      </td>
      <td>
        ${escapeHtml(displayEmail(contact))}
        <div class="muted">${escapeHtml(contact.email_status || "")}</div>
      </td>
      <td><span class="badge ${escapeHtml(contact.status)}">${escapeHtml(statusLabel(contact.status))}</span></td>
      <td>${contact.sequence_step || 0}</td>
      <td>${renderEmailFeedback(contact)}</td>
      <td>${formatDate(contact.last_contacted_at)}</td>
      <td class="error-text" title="${escapeHtml(contact.enrich_error || "")}">${escapeHtml(contact.enrich_error || "")}</td>
    </tr>
  `).join("");
}

function fullName(contact) {
  return [contact.first_name, contact.last_name].filter(Boolean).join(" ") || "(No name)";
}

function displayEmail(contact) {
  if (!contact.email || String(contact.email).includes("*")) return "待富化";
  return contact.email;
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
  const data = await api(`/api/${action}`, { method: "POST", body: JSON.stringify({}) });
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
    const outcome = result.result || [0, 0];
    showNotice(`自动获客完成：新增 ${outcome[0]} 条，重复 ${outcome[1]} 条`);
    await refresh();
  } catch (error) {
    showNotice(error.message, "error");
  }
});

refresh();
