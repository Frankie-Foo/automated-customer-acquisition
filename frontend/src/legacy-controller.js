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

function renderAccount() {
  const user = state.user;
  const usage = state.usage || {};
  if (!user) {
    accountName.textContent = "未登录";
    quotaStatus.textContent = "今日配额 --";
    adminConsole?.classList.add("hidden");
    adminNavLink?.classList.add("hidden");
    return;
  }

  accountName.textContent = user.display_name || user.username;
  quotaStatus.textContent = `获客 ${usage.source_count || 0}/${user.daily_source_limit} · 发信 ${usage.send_count || 0}/${user.daily_send_limit}`;
  adminConsole?.classList.toggle("hidden", user.role !== "admin");
  adminNavLink?.classList.toggle("hidden", user.role !== "admin");
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

window.addEventListener("salesbot:session", (event) => {
  state.user = event.detail?.user || null;
  state.usage = event.detail?.usage || null;
  renderAccount();
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

document.querySelectorAll(".nav a").forEach((link) => {
  link.addEventListener("click", () => {
    document.querySelectorAll(".nav a").forEach((item) => item.classList.remove("active"));
    link.classList.add("active");
  });
});

api("/api/me")
  .then((session) => {
    state.user = session.user;
    state.usage = session.usage;
    renderAccount();
  })
  .catch(() => renderAccount());
