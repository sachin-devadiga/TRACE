import { Outlet, useLocation } from "react-router-dom";
import { AlertTriangle } from "lucide-react";
import { Sidebar } from "./sidebar";
import { Topbar } from "./topbar";
import { useDataMode, useHealth } from "@/hooks/use-trace";

const titles: Record<string, string> = {
  "/": "Overview",
  "/new": "New audit",
  "/review": "Review queue",
  "/filings": "Filings",
  "/accuracy": "Accuracy",
  "/audit": "Audit log",
  "/settings": "Settings",
};

/**
 * A misconfigured deployment (missing VITE_API_BASE, backend down, CORS
 * blocked) must never look like an empty dashboard. Say what is wrong.
 */
function BackendStatusBanner() {
  const { mode } = useDataMode();
  const { data: health, error, isError } = useHealth();

  if (mode !== "http") return null;

  const apiBase = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";
  if (!apiBase) {
    return (
      <div
        className="mb-4 rounded-xl border border-danger/40 bg-danger/10 p-4 text-sm text-danger"
        data-testid="backend-banner"
      >
        <div className="flex items-start gap-2">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <div>
            <div className="font-semibold">The API address is not configured</div>
            <div className="mt-1">
              Set <code className="rounded bg-muted px-1">VITE_API_BASE</code> to the backend URL (for example
              {" "}<code className="rounded bg-muted px-1">https://trace-backend.onrender.com</code>) and rebuild.
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (isError || (health && health.status !== "ok" && health.status !== "healthy")) {
    return (
      <div
        className="mb-4 rounded-xl border border-danger/40 bg-danger/10 p-4 text-sm text-danger"
        data-testid="backend-banner"
      >
        <div className="flex items-start gap-2">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <div>
            <div className="font-semibold">The TRACE backend is unreachable</div>
            <div className="mt-1">
              {error instanceof Error ? error.message : "The API did not answer."} — it is configured as{" "}
              <code className="rounded bg-muted px-1">{apiBase}</code>. Check that the API service is running and
              that <code className="rounded bg-muted px-1">CORS_ORIGINS</code> on the backend includes this origin.
            </div>
          </div>
        </div>
      </div>
    );
  }

  return null;
}

export function AppShell() {
  const location = useLocation();
  const pageTitle = titles[location.pathname] ?? "TRACE";

  return (
    <div className="flex min-h-screen bg-background text-foreground">
      <Sidebar />
      <div className="flex min-h-screen flex-1 flex-col md:pl-64">
        <Topbar />
        <main id="main-content" className="flex-1 px-6 py-8">
          <div className="mb-6">
            <h1 className="font-sans text-2xl font-semibold tracking-tight text-foreground">{pageTitle}</h1>
          </div>
          <BackendStatusBanner />
          <Outlet />
        </main>
      </div>
    </div>
  );
}
