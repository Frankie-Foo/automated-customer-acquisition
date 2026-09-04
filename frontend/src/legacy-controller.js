import { api } from "./api.js";

const state = {
  user: window.SALESBOT_SESSION?.user || null,
  usage: window.SALESBOT_SESSION?.usage || null,
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
const workflowNav = document.querySelector("#workflow-nav");
const workflowLinks = Array.from(document.querySelectorAll("[data-flow-page]"));
const outreachViewButtons = Array.from(document.querySelectorAll("[data-outreach-view]"));
const customerWorkspace = document.querySelector("#customer-workspace");
const sentEmails = document.querySelector("#sent-emails");
let noticeTimer = null;

const pageMeta = {
  dashboard: ["我的工作", "先做系统排在最前面的任务。"],
  source: ["1 找客户", "上传名单，或按姓名、公司和职位搜索客户。"],
  research: ["2 准备客户", "系统自动补资料；你只处理可以发送和需要确认的客户。"],
  outreach: ["3 发邮件", "选择客户，生成并审核内容，然后发送。"],
  followup: ["4 跟进客户", "优先处理回复、已打开未回复和到期任务。"],
  report: ["团队数据", "查看获客、发送、回复和成交漏斗。"],
  admin: ["系统管理", "管理账号、额度、发件身份和系统状态。"],
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
  "customer-workspace": "outreach",
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
  const apolloLimit = Number(user.apollo_daily_credit_limit || 0);
  const apolloUsed = Number(usage.apollo_credits_used || 0) + Number(usage.apollo_credits_reserved || 0);
  quotaStatus.textContent = `获客 ${usage.source_count || 0}/${user.daily_source_limit} · 发信 ${usage.send_count || 0}/${user.daily_send_limit}${apolloLimit ? ` · 电话积分 ${apolloUsed}/${apolloLimit}` : ""}`;
  document.body.classList.toggle("is-admin", user.role === "admin");
  adminConsole?.classList.toggle("hidden", user.role !== "admin");
  adminNavLink?.classList.toggle("hidden", user.role !== "admin");
  if (user.role !== "admin" && user.role !== "manager" && currentPage() === "report") {
    setPage("dashboard", true);
  }
  if (user.role !== "admin" && currentPage() === "admin") {
    setPage("dashboard", true);
  }
}

function showNotice(message, type = "") {
  if (!notice || !message) return;
  window.clearTimeout(noticeTimer);
  notice.textContent = message;
  notice.className = `notice ${type}`.trim();
  notice.scrollIntoView({ behavior: "smooth", block: "nearest" });
  noticeTimer = window.setTimeout(hideNotice, type === "error" ? 10000 : 5000);
}

function hideNotice() {
  window.clearTimeout(noticeTimer);
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
  const previousPage = document.body.dataset.activePage;
  pages.forEach((node) => node.classList.toggle("active", node.dataset.page === safePage));
  pageLinks.forEach((link) => link.classList.toggle("active", link.dataset.pageLink === safePage));
  if (pageTitle) pageTitle.textContent = pageMeta[safePage][0];
  if (pageSubtitle) pageSubtitle.textContent = pageMeta[safePage][1];
  const inWorkflow = ["source", "research", "outreach", "followup"].includes(safePage);
  workflowNav?.classList.toggle("hidden", !inWorkflow);
  workflowLinks.forEach((link) => {
    const active = link.dataset.flowPage === safePage;
    link.classList.toggle("active", active);
    link.toggleAttribute("aria-current", active);
  });
  document.body.dataset.activePage = safePage;

  if (previousPage && previousPage !== safePage) {
    hideNotice();
    document.documentElement.scrollTop = 0;
    document.body.scrollTop = 0;
  }

  if (replaceHash && window.location.hash !== `#${safePage}`) {
    history.replaceState(null, "", `#${safePage}`);
  }
  window.dispatchEvent(new CustomEvent("salesbot:page-change", { detail: { page: safePage } }));
}

function syncPageFromHash() {
  const key = window.location.hash.replace("#", "");
  const page = currentPage();
  if (page === "admin" && state.user && state.user.role !== "admin") {
    setPage("dashboard", true);
    return;
  }
  if (page === "report" && state.user && !["admin", "manager"].includes(state.user.role)) {
    setPage("dashboard", true);
    return;
  }
  setPage(page, key !== page);
  if (page === "outreach") setOutreachView(["emails", "sent-emails"].includes(key) ? "history" : "workspace");
}

function setOutreachView(view) {
  const safeView = view === "history" ? "history" : "workspace";
  customerWorkspace?.classList.toggle("hidden", safeView !== "workspace");
  sentEmails?.classList.toggle("hidden", safeView !== "history");
  outreachViewButtons.forEach((button) => button.classList.toggle("active", button.dataset.outreachView === safeView));
  window.dispatchEvent(new CustomEvent("salesbot:outreach-view", { detail: { view: safeView } }));
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
  try {
    const response = await fetch("/api/logout", { method: "POST", credentials: "same-origin" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
  } catch (error) {
    showNotice(`退出失败，请重试：${error.message}`, "error");
    return;
  }
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
    if ((page === "admin" || (page === "report" && state.user?.role !== "manager")) && state.user?.role !== "admin") {
      event.preventDefault();
      showNotice("只有管理员可以打开控制台。", "error");
      return;
    }
    if (page === "outreach") setOutreachView("workspace");
    setPage(page);
  });
});

outreachViewButtons.forEach((button) => {
  button.addEventListener("click", () => setOutreachView(button.dataset.outreachView));
});

window.addEventListener("salesbot:open-contact", () => setOutreachView("workspace"));

window.addEventListener("hashchange", syncPageFromHash);
renderAccount();
syncPageFromHash();

api("/api/me")
  .then((session) => {
    state.user = session.user;
    state.usage = session.usage;
    renderAccount();
    syncPageFromHash();
  })
  .catch(() => renderAccount());
