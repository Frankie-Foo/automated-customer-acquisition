import { useEffect } from "react";
import AuthGatePortal from "./AuthGate.jsx";
import AdminConsolePortal from "./AdminConsole.jsx";
import ContactsPipelinePortal from "./ContactsPipeline.jsx";
import legacyMarkup from "./legacyMarkup.html?raw";
import "./legacy-styles.css";

export default function App() {
  useEffect(() => {
    let mounted = true;
    window.SALESBOT_REACT_AUTH = true;
    window.SALESBOT_REACT_ADMIN = true;
    window.SALESBOT_REACT_CONTACTS = true;
    import("./legacy-controller.js").catch((error) => {
      if (!mounted) return;
      console.error("Failed to load dashboard controller", error);
    });
    return () => {
      mounted = false;
    };
  }, []);

  return (
    <>
      <div dangerouslySetInnerHTML={{ __html: legacyMarkup }} />
      <AuthGatePortal />
      <AdminConsolePortal />
      <ContactsPipelinePortal />
    </>
  );
}
