import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "./api.js";

export default function AuthGatePortal({ onSessionChange }) {
  const [target, setTarget] = useState(null);

  useEffect(() => {
    setTarget(document.querySelector("#login-screen"));
  }, []);

  if (!target) return null;
  return createPortal(<AuthGate onSessionChange={onSessionChange} />, target);
}

function AuthGate({ onSessionChange }) {
  const [mode, setMode] = useState("checking");
  const [user, setUser] = useState(null);
  const [usage, setUsage] = useState(null);
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const screen = document.querySelector("#login-screen");
    if (!screen) return;
    screen.classList.toggle("hidden", mode === "checking" || mode === "authenticated");
    const locked = mode !== "authenticated";
    document.body.classList.toggle("auth-locked", locked);
    for (const node of document.querySelectorAll(".sidebar, main")) {
      node.inert = locked;
      node.setAttribute("aria-hidden", locked ? "true" : "false");
    }
    return () => document.body.classList.remove("auth-locked");
  }, [mode]);

  useEffect(() => {
    const vpsParams = readVpsParams();
    async function boot() {
      try {
        const session = vpsParams
          ? await api("/api/auth/vps-login", {
              method: "POST",
              body: JSON.stringify({ sessionID: vpsParams.sessionId, userId: vpsParams.userId }),
            })
          : await api("/api/me");
        setUser(session.user);
        setUsage(session.usage);
        publishSession(session.user, session.usage, onSessionChange);
        if (vpsParams) cleanVpsParams(session.next || "/");
        setMode(session.user.must_change_password ? "change-password" : "authenticated");
        if (!session.user.must_change_password) {
          window.dispatchEvent(new CustomEvent("salesbot:refresh"));
        }
      } catch (err) {
        publishSession(null, null, onSessionChange);
        if (vpsParams) setError(err.message);
        setMode("login");
      }
    }
    boot();
  }, [onSessionChange]);

  useEffect(() => {
    const handleLogout = () => {
      setUser(null);
      setUsage(null);
      setPassword("");
      publishSession(null, null, onSessionChange);
      setMode("login");
    };
    const handleUnauthorized = () => {
      setMode("login");
    };
    window.addEventListener("salesbot:logout", handleLogout);
    window.addEventListener("salesbot:unauthorized", handleUnauthorized);
    return () => {
      window.removeEventListener("salesbot:logout", handleLogout);
      window.removeEventListener("salesbot:unauthorized", handleUnauthorized);
    };
  }, [onSessionChange]);

  async function login(event) {
    event.preventDefault();
    setError("");
    try {
      const session = await api("/api/login", {
        method: "POST",
        body: JSON.stringify({ username: username.trim(), password }),
      });
      setUser(session.user);
      setUsage(session.usage);
      setCurrentPassword(password);
      publishSession(session.user, session.usage, onSessionChange);
      if (session.user.must_change_password) {
        setMode("change-password");
        return;
      }
      setMode("authenticated");
      window.dispatchEvent(new CustomEvent("salesbot:refresh"));
    } catch (err) {
      setError(err.message);
    }
  }

  async function changePassword(event) {
    event.preventDefault();
    setError("");
    try {
      if (newPassword.length < 12) throw new Error("新密码至少 12 位");
      if (newPassword !== confirmPassword) throw new Error("两次输入的新密码不一致");
      const result = await api("/api/change-password", {
        method: "POST",
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });
      setUser(result.user);
      publishSession(result.user, usage, onSessionChange);
      setPassword("");
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setMode("authenticated");
      window.dispatchEvent(new CustomEvent("salesbot:notice", { detail: { message: "密码已更新" } }));
      window.dispatchEvent(new CustomEvent("salesbot:refresh"));
    } catch (err) {
      setError(err.message);
    }
  }

  if (mode === "checking" || mode === "authenticated") {
    return null;
  }

  return (
    <>
      {mode === "login" && (
        <form className="login-card" onSubmit={login}>
          <div className="mark">LA</div>
          <h1>登录获客系统</h1>
          <p>请输入分配给你的账号。管理员默认账号仅用于初始化，上线前需要改密码。</p>
          <label htmlFor="login-username">
            账号
            <input id="login-username" name="username" autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} />
          </label>
          <label htmlFor="login-password">
            密码
            <input id="login-password" name="password" type="password" autoComplete="current-password" placeholder="输入密码" value={password} onChange={(event) => setPassword(event.target.value)} />
          </label>
          <button className="primary" type="submit">登录</button>
          <div className="login-error">{error}</div>
        </form>
      )}
      {mode === "change-password" && (
        <form className="login-card" onSubmit={changePassword}>
          <div className="mark">LA</div>
          <h1>首次登录请修改密码</h1>
          <p>为了账号安全，请把管理员分配的临时密码改成你自己的密码。</p>
          <label htmlFor="current-password">
            当前临时密码
            <input id="current-password" name="current_password" type="password" autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} />
          </label>
          <label htmlFor="new-password">
            新密码
            <input id="new-password" name="new_password" type="password" autoComplete="new-password" placeholder="至少 12 位" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} />
          </label>
          <label htmlFor="confirm-password">
            确认新密码
            <input id="confirm-password" name="confirm_password" type="password" autoComplete="new-password" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} />
          </label>
          <button className="primary" type="submit">保存新密码</button>
          <div className="login-error">{error}</div>
        </form>
      )}
    </>
  );
}

function publishSession(user, usage, onSessionChange) {
  window.SALESBOT_SESSION = { user, usage };
  onSessionChange?.(user || null);
  window.dispatchEvent(new CustomEvent("salesbot:session", { detail: { user, usage } }));
}

function readVpsParams() {
  const direct = new URLSearchParams(window.location.search);
  const sessionId = direct.get("session_id");
  const userId = direct.get("user_id");
  if (sessionId && userId) return { sessionId, userId };
  const next = direct.get("next");
  if (!next) return null;
  try {
    const nested = new URLSearchParams(new URL(next, window.location.origin).search);
    const nestedSessionId = nested.get("session_id");
    const nestedUserId = nested.get("user_id");
    if (nestedSessionId && nestedUserId) return { sessionId: nestedSessionId, userId: nestedUserId };
  } catch {
    return null;
  }
  return null;
}

function cleanVpsParams(next) {
  const target = new URL(next || "/", window.location.origin);
  window.history.replaceState(null, "", `${target.pathname}${target.search}${window.location.hash || "#dashboard"}`);
}
