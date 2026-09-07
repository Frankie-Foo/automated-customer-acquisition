if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", async () => {
    try {
      const registration = await navigator.serviceWorker.register("/static/sw.js", { scope: "/" });
      const notifyUpdate = () => {
        if (registration.waiting) window.dispatchEvent(new Event("salesbot:pwa-update"));
      };
      notifyUpdate();
      registration.addEventListener("updatefound", () => {
        const worker = registration.installing;
        worker?.addEventListener("statechange", () => {
          if (worker.state === "installed" && navigator.serviceWorker.controller) {
            window.dispatchEvent(new Event("salesbot:pwa-update"));
          }
        });
      });
      let lastCheck = 0;
      const checkForUpdate = async () => {
        notifyUpdate();
        if (!navigator.onLine || document.visibilityState !== "visible" || Date.now() - lastCheck < 15 * 60 * 1000) return;
        lastCheck = Date.now();
        try {
          await registration.update();
          notifyUpdate();
        } catch (error) {
          console.warn("PWA update check failed", error);
        }
      };
      document.addEventListener("visibilitychange", checkForUpdate);
      window.addEventListener("online", checkForUpdate);
    } catch (error) {
      console.warn("PWA registration failed", error);
    }
  });
}
