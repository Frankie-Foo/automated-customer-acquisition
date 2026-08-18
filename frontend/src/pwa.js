if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", async () => {
    try {
      const registration = await navigator.serviceWorker.register("/static/sw.js", { scope: "/" });
      registration.addEventListener("updatefound", () => {
        const worker = registration.installing;
        worker?.addEventListener("statechange", () => {
          if (worker.state === "installed" && navigator.serviceWorker.controller) {
            window.dispatchEvent(new Event("salesbot:pwa-update"));
          }
        });
      });
    } catch (error) {
      console.warn("PWA registration failed", error);
    }
  });
}
