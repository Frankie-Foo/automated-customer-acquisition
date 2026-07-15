import { memo, useEffect, useState } from "react";
import AuthGatePortal from "./AuthGate.jsx";
import AdminConsolePortal from "./AdminConsole.jsx";
import ContactsPipelinePortal from "./ContactsPipeline.jsx";
import CustomerWorkspacePortal from "./CustomerWorkspace.jsx";
import WorkbenchPortal from "./Workbench.jsx";
import DashboardViewsPortal from "./DashboardViews.jsx";
import SentEmailsPortal from "./SentEmails.jsx";
import legacyMarkup from "./legacyMarkup.html?raw";
import "./legacy-styles.css";

const routeAliases = {
  "": "dashboard",
  sourcing: "source",
  workbench: "source",
  pipeline: "research",
  "customer-list": "research",
  emails: "outreach",
  "sent-emails": "outreach",
  followups: "followup",
  lifecycle: "followup",
  "lifecycle-board": "followup",
  "customer-workspace": "outreach",
  "ops-report": "report",
  readiness: "report",
  "admin-console": "admin",
};

function currentPage() {
  const key = window.location.hash.replace("#", "");
  return routeAliases[key] || key || "dashboard";
}

const LegacyShell = memo(function LegacyShell() {
  return <div className="app-shell" dangerouslySetInnerHTML={{ __html: legacyMarkup }} />;
});

export default function App() {
  const [sessionUser, setSessionUser] = useState(null);
  const [activePage, setActivePage] = useState(currentPage);
  const [visitedPages, setVisitedPages] = useState(() => new Set([currentPage()]));

  useEffect(() => {
    const syncRoute = () => {
      const page = currentPage();
      setActivePage(page);
      setVisitedPages((current) => current.has(page) ? current : new Set([...current, page]));
    };
    window.addEventListener("hashchange", syncRoute);
    window.addEventListener("salesbot:page-change", syncRoute);
    syncRoute();
    return () => {
      window.removeEventListener("hashchange", syncRoute);
      window.removeEventListener("salesbot:page-change", syncRoute);
    };
  }, []);

  useEffect(() => {
    if (!sessionUser || sessionUser.must_change_password) return undefined;
    let mounted = true;
    window.SALESBOT_REACT_AUTH = true;
    window.SALESBOT_REACT_ADMIN = true;
    window.SALESBOT_REACT_CONTACTS = true;
    window.SALESBOT_REACT_WORKSPACE = true;
    window.SALESBOT_REACT_WORKBENCH = true;
    window.SALESBOT_REACT_DASHBOARD = true;
    window.SALESBOT_REACT_SENT_EMAILS = true;
    import("./legacy-controller.js").catch((error) => {
      if (!mounted) return;
      console.error("Failed to load dashboard controller", error);
    });
    return () => {
      mounted = false;
    };
  }, [sessionUser]);

  return (
    <>
      <LegacyShell />
      <AuthGatePortal onSessionChange={setSessionUser} />
      {sessionUser && !sessionUser.must_change_password && (
        <>
          {visitedPages.has("admin") && <AdminConsolePortal />}
          {visitedPages.has("research") && <ContactsPipelinePortal />}
          {visitedPages.has("outreach") && <CustomerWorkspacePortal />}
          {visitedPages.has("source") && <WorkbenchPortal />}
          <DashboardViewsPortal activePage={activePage} />
          {visitedPages.has("outreach") && <SentEmailsPortal />}
        </>
      )}
    </>
  );
}
