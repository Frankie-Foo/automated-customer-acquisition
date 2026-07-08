import { api } from "./api.js";

const state = {
  user: null,
  usage: null,
};

const notice = document.querySelector("#notice");
const accountName = document.querySelector("#account-name");
const quotaStatus = document.querySelector("#quota-status");
const logoutButton = document.querySelector("#logout-button");
const exportButton = document.querySelector("#export-button");
const refreshButton = document.querySelector("#refresh-button");
const adminConsole = document.querySelector("#admin-console");
const adminNavLink = document.querySelector("#nav-admin-link");
const pageTitle = document.querySelector("#page-title");
const pageSubtitle = document.querySelector("#page-subtitle");
const pageLinks = Array.from(document.querySelectorAll("[data-page-link]"));
const pages = Array.from(document.querySelectorAll("[data-page]"));

const pageMeta = {
  dashboard: ["工作台", "按 AI 员工流水线推进：找客户、查客户、写邮件、做跟进、出周报。"],
  source: ["市场与获客", "市场情报员和客户搜索员负责拆方向、导入种子、搜索联系人和补邮箱。"],
  research: ["客户背调", "公司背调员沉淀客户画像、匹配度、风险点和建议打法。"],
  outreach: ["邮件触达", "开发信助理管理已发送邮件、发件身份、邮件内容和回流状态。"],
  followup: ["跟进任务", "跟进提醒员负责打开未回复、已回复、退信处理和 SABCD 推进。"],
  report: ["主管周报", "主管周报员汇总团队获客、发信、打开、回复、退信和重点客户。"],
  admin: ["管理员控制台", "创建账号、调整配额、配置发件池、检查生产状态。"],
};

const hashPageMap = {
  "": "dashboard",
  dashboard: "dashboard",
  "ops-report": "report",
  readiness: "report",
  source: "source",
  sourcing: "source",
  workbench: "source",
  research: "research",
  pipeline: "research",
  "customer-list": "research",
  outreach: "outreach",
  emails: "outreach",
  "sent-emails": "outreach",
  followup: "followup",
  followups: "followup",
  lifecycle: "followup",
  "lifecycle-board": "followup",
  "customer-workspace": "followup",
  report: "report",
  admin: "admin",
  "admin-console": "admin",
};

function renderAccount() {
  const user = state.user;
  const usage = state.usage || {};
  if (!user) {
    accountName.textContent = "未登录";
    quotaStatus.textContent = "今日配额 --";
    adminConsole?.classList.add("hidden");
    adminNavLink?.classList.add("hidden");
    document.body.classList.remove("is-admin");
    return;
  }

  accountName.textContent = user.display_name || user.username;
  quotaStatus.textContent = `获客 ${usage.source_count || 0}/${user.daily_source_limit} · 发信 ${usage.send_count || 0}/${user.daily_send_limit}`;
  document.body.classList.toggle("is-admin", user.role === "admin");
  adminConsole?.classList.toggle("hidden", user.role !== "admin");
  adminNavLink?.classList.toggle("hidden", user.role !== "admin");
  if (user.role !== "admin" && currentPage() === "admin") {
    setPage("dashboard", true);
  }
}

function showNotice(message, type = "") {
  if (!notice || !message) return;
  notice.textContent = message;
  notice.className = `notice ${type}`.trim();
  notice.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function hideNotice() {
  notice?.classList.add("hidden");
}

function refreshAll() {
  hideNotice();
  window.dispatchEvent(new CustomEvent("salesbot:contacts-refresh"));
  window.dispatchEvent(new CustomEvent("salesbot:refresh-related"));
  window.dispatchEvent(new CustomEvent("salesbot:ops-refresh"));
}

function currentPage() {
  const key = window.location.hash.replace("#", "");
  return hashPageMap[key] || "dashboard";
}

function setPage(page, replaceHash = false) {
  const safePage = pageMeta[page] ? page : "dashboard";
  pages.forEach((node) => node.classList.toggle("active", node.dataset.page === safePage));
  pageLinks.forEach((link) => link.classList.toggle("active", link.dataset.pageLink === safePage));
  if (pageTitle) pageTitle.textContent = pageMeta[safePage][0];
  if (pageSubtitle) pageSubtitle.textContent = pageMeta[safePage][1];

  if (replaceHash && window.location.hash !== `#${safePage}`) {
    history.replaceState(null, "", `#${safePage}`);
  }
}

function syncPageFromHash() {
  const key = window.location.hash.replace("#", "");
  const page = currentPage();
  if (page === "admin" && state.user && state.user.role !== "admin") {
    setPage("dashboard", true);
    return;
  }
  setPage(page, key !== page);
}

window.addEventListener("salesbot:session", (event) => {
  state.user = event.detail?.user || null;
  state.usage = event.detail?.usage || null;
  renderAccount();
  syncPageFromHash();
});

window.addEventListener("salesbot:usage", (event) => {
  state.usage = event.detail?.usage || state.usage;
  renderAccount();
});

window.addEventListener("salesbot:refresh", refreshAll);

window.addEventListener("salesbot:notice", (event) => {
  if (event.detail?.message) {
    showNotice(event.detail.message, event.detail.type || "");
  }
});

refreshButton?.addEventListener("click", refreshAll);

logoutButton?.addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST", credentials: "same-origin" }).catch(() => {});
  state.user = null;
  state.usage = null;
  renderAccount();
  window.dispatchEvent(new CustomEvent("salesbot:logout"));
});

exportButton?.addEventListener("click", () => {
  window.location.href = "/api/export.csv";
});

pageLinks.forEach((link) => {
  link.addEventListener("click", (event) => {
    const page = link.dataset.pageLink;
    if (!page) return;
    if (page === "admin" && state.user?.role !== "admin") {
      event.preventDefault();
      showNotice("只有管理员可以打开控制台。", "error");
      return;
    }
    setPage(page);
  });
});

window.addEventListener("hashchange", syncPageFromHash);
syncPageFromHash();

api("/api/me")
  .then((session) => {
    state.user = session.user;
    state.usage = session.usage;
    renderAccount();
    syncPageFromHash();
  })
  .catch(() => renderAccount());
