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

const pageMeta = {
  dashboard: ["工作台", "查看今天最需要处理的客户和邮件动作。"],
  source: ["1 获取线索", "选择一种方式搜索或导入客户，完成后进入客户核验。"],
  research: ["2 核验客户", "领取客户，核对身份、邮箱、社媒和客户画像。"],
  outreach: ["3 邮件触达", "检查邮件内容并发送，随后查看送达、打开、回复和退信。"],
  followup: ["4 跟进推进", "处理回复和待办，把客户持续推进到下一阶段。"],
  report: ["运营周报", "汇总获客、有效邮箱、发送、打开、回复、退信和 Provider 成本。"],
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
  if (user.role !== "admin" && ["admin", "report"].includes(currentPage())) {
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
  const previousPage = document.body.dataset.activePage;
  pages.forEach((node) => node.classList.toggle("active", node.dataset.page === safePage));
  pageLinks.forEach((link) => link.classList.toggle("active", link.dataset.pageLink === safePage));
  if (pageTitle) pageTitle.textContent = pageMeta[safePage][0];
  if (pageSubtitle) pageSubtitle.textContent = pageMeta[safePage][1];
  const inWorkflow = ["source", "research", "outreach", "followup"].includes(safePage);
  workflowNav?.classList.toggle("hidden", !inWorkflow);
  workflowLinks.forEach((link) => link.classList.toggle("active", link.dataset.flowPage === safePage));
  document.body.dataset.activePage = safePage;

  if (previousPage && previousPage !== safePage) {
    document.documentElement.scrollTop = 0;
    document.body.scrollTop = 0;
  }

  if (replaceHash && window.location.hash !== `#${safePage}`) {
    history.replaceState(null, "", `#${safePage}`);
  }
}

function syncPageFromHash() {
  const key = window.location.hash.replace("#", "");
  const page = currentPage();
  if (["admin", "report"].includes(page) && state.user && state.user.role !== "admin") {
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
    if (["admin", "report"].includes(page) && state.user?.role !== "admin") {
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
