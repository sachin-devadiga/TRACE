import { FlaskConical } from "lucide-react";
import { useDataMode } from "@/hooks/use-trace";
import { cn } from "@/lib/utils";
import secSnapshot from "@/api/mock/fixtures/sec-snapshot.json";

export function DemoPill() {
  const { mode, healthStatus } = useDataMode();
  const live = mode === "http";
  const retrieved = (secSnapshot as any)?.retrieved_at ? new Date((secSnapshot as any).retrieved_at).toISOString().slice(0, 10) : "";
  return (
    <span
      data-testid="demo-pill"
      className={cn(
        "inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium",
        live ? "border-success/40 bg-success/10 text-success" : "border-warning/40 bg-warning/10 text-warning"
      )}
      title={live ? "Connected to the TRACE API" : "Using SEC snapshot data"}
    >
      <span
        className={cn("h-1.5 w-1.5 rounded-full", live ? "bg-success" : "bg-warning", healthStatus === "ok" && !live ? "animate-pulse" : "")}
        aria-hidden="true"
      />
      {live ? "Live" : `SEC snapshot (retrieved ${retrieved})`}
    </span>
  );
}

export function DemoPillFooter() {
  const { mode } = useDataMode();
  const retrieved = (secSnapshot as any)?.retrieved_at ? new Date((secSnapshot as any).retrieved_at).toISOString().slice(0, 10) : "";
  return (
    <span className="text-xs text-muted-foreground">{mode === "http" ? "Live API" : `SEC snapshot (retrieved ${retrieved})`}</span>
  );
}

export function FlaskConicalIcon() {
  return <FlaskConical className="h-4 w-4" aria-hidden="true" />;
}
