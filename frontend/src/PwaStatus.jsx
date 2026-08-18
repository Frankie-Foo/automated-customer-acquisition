import { useEffect, useState } from "react";

export default function PwaStatus() {
  const [online, setOnline] = useState(navigator.onLine);
  const [installPrompt, setInstallPrompt] = useState(null);
  const [updateReady, setUpdateReady] = useState(false);

  useEffect(() => {
    const onlineHandler = () => setOnline(true);
    const offlineHandler = () => setOnline(false);
    const installHandler = (event) => {
      event.preventDefault();
      setInstallPrompt(event);
    };
    const updateHandler = () => setUpdateReady(true);
    window.addEventListener("online", onlineHandler);
    window.addEventListener("offline", offlineHandler);
    window.addEventListener("beforeinstallprompt", installHandler);
    window.addEventListener("salesbot:pwa-update", updateHandler);
    return () => {
      window.removeEventListener("online", onlineHandler);
      window.removeEventListener("offline", offlineHandler);
      window.removeEventListener("beforeinstallprompt", installHandler);
      window.removeEventListener("salesbot:pwa-update", updateHandler);
    };
  }, []);

  const install = async () => {
    if (!installPrompt) return;
    await installPrompt.prompt();
    setInstallPrompt(null);
  };

  const update = async () => {
    const registration = await navigator.serviceWorker?.getRegistration();
    if (!registration?.waiting) {
      window.location.reload();
      return;
    }
    navigator.serviceWorker.addEventListener("controllerchange", () => window.location.reload(), { once: true });
    registration.waiting.postMessage({ type: "SKIP_WAITING" });
  };

  if (online && !installPrompt && !updateReady) return null;

  return (
    <aside className={`pwa-status ${online ? "" : "is-offline"}`} aria-live="polite">
      {!online && <span>网络已断开，获客、富化和发信暂不可用。</span>}
      {online && updateReady && <><span>新版本已准备好。</span><button type="button" onClick={update}>立即更新</button></>}
      {online && !updateReady && installPrompt && <><span>可安装为桌面应用。</span><button type="button" onClick={install}>安装</button></>}
    </aside>
  );
}
