import { useState } from "react";
import { ShieldAlert, Trash2 } from "lucide-react";
import { useDataMode, useHealth, useResetDemo, useSetFailureMode, useTamperWithLastRow } from "@/hooks/use-trace";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useTheme } from "@/components/theme-provider";
import { toast } from "sonner";

export function SettingsPage() {
  const { mode } = useDataMode();
  const { data: health, refetch: refetchHealth, isFetching: checking } = useHealth();
  const resetDemo = useResetDemo();
  const setFailureMode = useSetFailureMode();
  const tamper = useTamperWithLastRow();
  const { theme, toggleTheme } = useTheme();
  const [confirmReset, setConfirmReset] = useState(false);
  const [failureMode, setFailureModeState] = useState(false);

  const connectionOk = health?.status === "ok" || health?.status === "healthy";

  const testConnection = async () => {
    try {
      const result = await refetchHealth();
      if (result.data?.status === "ok" || result.data?.status === "healthy") {
        toast.success(`Backend reachable — ${result.data.version ?? "unknown version"}.`);
      } else {
        toast.error("The backend answered but did not report a healthy status.");
      }
    } catch (error) {
      toast.error(
        error instanceof Error
          ? `Cannot reach the backend: ${error.message}`
          : "Cannot reach the backend. Is the API server running on port 8000?",
      );
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold">Settings</h2>
        <p className="text-sm text-muted-foreground">Data source, policy defaults, and demo controls.</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Data source</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-sm">Current mode</span>
            <Badge variant={mode === "mock" ? "warning" : "success"}>{mode === "mock" ? "SEC snapshot" : "Live"}</Badge>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm">Connection status</span>
            <span className="text-sm font-medium text-success">{connectionOk ? "Healthy" : "Unreachable"}</span>
          </div>
          {health?.storage ? (
            <div className="flex items-center justify-between">
              <span className="text-sm">Storage backend</span>
              <span className="text-sm font-medium" data-testid="storage-backend">
                {health.storage}
              </span>
            </div>
          ) : null}
          {mode === "http" ? (
            <div className="text-sm text-muted-foreground">Base URL: {import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000"}</div>
          ) : null}
          <Button variant="outline" size="sm" onClick={() => void testConnection()} disabled={checking}>
            {checking ? "Testing…" : "Test connection"}
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Policy defaults</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between">
            <span className="text-sm">Default confidence threshold</span>
            <span className="rounded-full bg-accent px-2 py-0.5 text-sm font-semibold">85%</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm">Default YoY tolerance</span>
            <span className="rounded-full bg-accent px-2 py-0.5 text-sm font-semibold">30%</span>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Controls</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm font-medium">Simulate failure</div>
              <div className="text-xs text-muted-foreground">Force new audits into the FAILED state.</div>
            </div>
            <Switch
              checked={failureMode}
              onCheckedChange={(v) => {
                setFailureModeState(v);
                void setFailureMode
                  .mutateAsync(v)
                  .then(() =>
                    toast.success(
                      v
                        ? "Failure simulation enabled — new audits will fail on purpose."
                        : "Failure simulation disabled.",
                    ),
                  )
                  .catch((error: unknown) => {
                    setFailureModeState(!v);
                    toast.error(
                      error instanceof Error
                        ? `Could not change the failure mode: ${error.message}`
                        : "Could not change the failure mode.",
                    );
                  });
              }}
              aria-label="Simulate failure"
              data-testid="failure-mode-switch"
            />
          </div>
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm font-medium">Tamper with last audit row</div>
              <div className="text-xs text-muted-foreground">
                Development only — inserts a synthetic row to show how the hash chain detects tampering. It is disabled
                on this server unless <code className="rounded bg-muted px-1">DEMO_TAMPER_ENABLED=true</code> is set.
              </div>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                void tamper
                  .mutateAsync()
                  .then((r) =>
                    r.verified
                      ? toast.success(
                          "Tampering is disabled on this server, so the chain is still intact. Set DEMO_TAMPER_ENABLED=true to enable the demo.",
                        )
                      : toast.error(`Chain broken at ${r.broken_row_id}`),
                  )
                  .catch((error: unknown) =>
                    toast.error(
                      error instanceof Error
                        ? `Tamper check failed: ${error.message}`
                        : "Tamper check failed.",
                    ),
                  )
              }
            >
              <ShieldAlert className="h-4 w-4" aria-hidden="true" /> Tamper
            </Button>
          </div>
          {tamper.data ? (
            <div className={`rounded-xl border p-3 text-sm ${tamper.data.verified ? "border-success/40 bg-success/10 text-success" : "border-danger/40 bg-danger/10 text-danger"}`}>
              {tamper.data.verified ? "Chain intact" : `Chain broken at ${tamper.data.broken_row_id}`}
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Appearance</CardTitle>
        </CardHeader>
        <CardContent className="flex items-center justify-between">
          <span className="text-sm">Theme</span>
          <Button variant="outline" size="sm" onClick={toggleTheme}>
            Switch to {theme === "dark" ? "light" : "dark"} theme
          </Button>
        </CardContent>
      </Card>

      <Card className="border-danger/40">
        <CardHeader>
          <CardTitle className="text-danger">Danger zone</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm font-medium">Reset data</div>
              <div className="text-xs text-muted-foreground">
                Clears every audit thread from the workflow store. The append-only audit log is never deleted — it is
                the permanent record of what was decided.
              </div>
            </div>
            <Button variant="destructive" size="sm" onClick={() => setConfirmReset(true)}>
              <Trash2 className="h-4 w-4" aria-hidden="true" /> Reset
            </Button>
          </div>
        </CardContent>
      </Card>

      <Dialog open={confirmReset} onOpenChange={setConfirmReset}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reset data?</DialogTitle>
            <DialogDescription>
              This removes every audit thread from the workflow store. The append-only audit log is kept as the permanent
              record. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmReset(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                void resetDemo
                  .mutateAsync()
                  .then(() => {
                    setConfirmReset(false);
                    toast.success("Data reset. The review queue has been cleared.");
                  })
                  .catch((error: unknown) => {
                    setConfirmReset(false);
                    toast.error(
                      error instanceof Error
                        ? `Reset failed: ${error.message}`
                        : "Reset failed.",
                    );
                  });
              }}
            >
              Reset demo data
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
