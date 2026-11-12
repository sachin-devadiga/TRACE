import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Check, ChevronRight, Circle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useStartAudit, useVerifyChain, useThreads } from "@/hooks/use-trace";
import { toast } from "sonner";

const steps = [
  { id: "run", label: "Run an audit" },
  { id: "inspect", label: "Inspect a flagged item" },
  { id: "decide", label: "Make a decision" },
  { id: "accuracy", label: "View accuracy" },
  { id: "verify", label: "Verify audit chain" },
];

export function GuidedDemo() {
  const [open, setOpen] = useState(false);
  const [done, setDone] = useState<Record<string, boolean>>({});
  const navigate = useNavigate();
  const startAudit = useStartAudit();
  const verifyChain = useVerifyChain();
  const { data: threads } = useThreads();

  // The checklist reflects the real state of the app instead of a local list.
  const hasFlagged = (threads ?? []).some((t) => t.flagged_count > 0);
  const hasCompleted = (threads ?? []).some(
    (t) => t.status === "COMPLETED" || t.status === "AUTO_APPROVED",
  );
  const hasDecision = (threads ?? []).some(
    (t) => t.flagged_count === 0 && t.status === "COMPLETED",
  );
  const stepState: Record<string, boolean> = {
    run: hasCompleted || hasFlagged || Boolean(done.run),
    inspect: hasFlagged || Boolean(done.inspect),
    decide: hasDecision || Boolean(done.decide),
    accuracy: hasCompleted || Boolean(done.accuracy),
    verify: Boolean(done.verify),
  };

  const runDemoAudit = async () => {
    try {
      const status = await startAudit.mutateAsync({
        cik: "0000320193",
        confidence_threshold: 0.85,
        yoy_threshold_pct: 0.3,
        demo_only: true,
      });
      setDone((prev) => ({ ...prev, run: true }));
      setOpen(false);
      toast.success("Demo audit started. It appears in the review queue when the filing is parsed.");
      navigate(`/review?thread=${status.thread_id}`);
    } catch (error) {
      toast.error(
        error instanceof Error
          ? `The demo audit could not be started: ${error.message}`
          : "The demo audit could not be started. Is the backend running?",
        { duration: 8000 },
      );
    }
  };

  const handleVerify = async () => {
    try {
      const result = await verifyChain.refetch();
      if (result.data?.verified) {
        setDone((prev) => ({ ...prev, verify: true }));
        toast.success(
          `Audit chain intact — ${result.data.checked_rows} of ${result.data.total_rows} records verified.`,
        );
      } else {
        toast.error(`Audit chain broken at row ${result.data?.broken_row_id ?? "unknown"}.`);
      }
    } catch (error) {
      toast.error(
        error instanceof Error
          ? `Could not verify the chain: ${error.message}`
          : "Could not verify the chain.",
      );
    }
  };

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)} data-testid="guided-demo-button">
        Guided demo
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Guided demo</DialogTitle>
            <DialogDescription>Walk through the full TRACE workflow in under two minutes.</DialogDescription>
          </DialogHeader>
          <ol className="space-y-2">
            {steps.map((s, index) => {
              const state = stepState[s.id] ? "done" : "todo";
              return (
                <li key={s.id} className="flex items-center gap-3 rounded-lg border p-3">
                  {state === "done" ? (
                    <Check className="h-4 w-4 text-success" aria-hidden="true" />
                  ) : (
                    <Circle className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                  )}
                  <span className="flex-1 text-sm font-medium">
                    {index + 1}. {s.label}
                  </span>
                  {state === "todo" ? (
                    <ChevronRight className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                  ) : null}
                </li>
              );
            })}
          </ol>
          <p className="text-xs text-muted-foreground">
            Step 1 starts a real audit against SEC EDGAR (about 15 seconds). Steps 2-4 are completed from the review
            queue, accuracy and audit-log pages.
          </p>
          <DialogFooter className="flex-col items-stretch gap-2 sm:flex-col">
            <Button onClick={runDemoAudit} disabled={startAudit.isPending} data-testid="guided-demo-run">
              {startAudit.isPending ? "Running…" : "Run demo audit"}
            </Button>
            <Button variant="outline" onClick={handleVerify} disabled={verifyChain.isFetching} data-testid="guided-demo-verify">
              {verifyChain.isFetching ? "Verifying…" : "Verify audit chain"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
