import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Play, Layers, Search } from "lucide-react";
import {
  useCompanies,
  useCompanySearch,
  useFilings,
  useStartAudit,
  useThreadStatus,
  useDataMode,
} from "@/hooks/use-trace";
import type { CompanyInfo } from "@/api/types";
import { Input } from "@/components/ui/input";
import { WorkflowStepper } from "@/components/workflow-stepper";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Slider } from "@/components/ui/slider";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

export function NewAuditPage() {
  const navigate = useNavigate();
  const { data: companies, isLoading } = useCompanies();
  const { mode } = useDataMode();
  const startAudit = useStartAudit();

  const [selected, setSelected] = useState<string[]>(["0000320193"]);
  const [manualCompany, setManualCompany] = useState<CompanyInfo | null>(null);
  const [query, setQuery] = useState("");
  const [accession, setAccession] = useState<string | undefined>(undefined);
  const [confidence, setConfidence] = useState(0.85);
  const [yoy, setYoY] = useState(0.3);
  const [demoOnly, setDemoOnly] = useState(false);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [batchProgress, setBatchProgress] = useState<{ done: number; total: number } | null>(null);
  const activeStatus = useThreadStatus(activeThreadId);

  const searchResults = useCompanySearch(query);
  const chosenCik = manualCompany?.cik ?? (selected.length === 1 ? selected[0] : undefined);
  const filings = useFilings(chosenCik, "10-K");
  const newestFiling = filings.data?.[0]?.accession_number;
  const effectiveAccession = accession ?? newestFiling;

  const errorMessage = (error: unknown) =>
    error instanceof Error
      ? error.message
      : "The backend did not respond. Check that the API server is running on port 8000.";

  const chooseCompany = (company: CompanyInfo) => {
    setSelected([company.cik]);
    setManualCompany(company);
    // A different filing history means the previous accession is invalid.
    setAccession(undefined);
  };

  const toggle = (cik: string) => {
    setManualCompany((prev) => (prev?.cik === cik ? null : prev));
    setSelected((prev) => (prev.includes(cik) ? prev.filter((c) => c !== cik) : [...prev, cik]));
    setAccession(undefined);
  };

  const runSingle = async (cik: string, accessionNumber?: string) => {
    try {
      const result = await startAudit.mutateAsync({
        cik,
        confidence_threshold: confidence,
        yoy_threshold_pct: yoy,
        demo_only: demoOnly,
        accession_number: accessionNumber,
      });
      setActiveThreadId(result.thread_id);
      toast.success(`Audit started for CIK ${cik}.`);
    } catch (error) {
      toast.error(`Audit could not be started: ${errorMessage(error)}`, { duration: 8000 });
    }
  };

  const runAll = async () => {
    const list = companies ?? [];
    const targets = list.filter((c) => selected.length === 0 || selected.includes(c.cik));
    if (targets.length === 0) {
      toast.error("Select at least one company first.");
      return;
    }
    setBatchProgress({ done: 0, total: targets.length });
    const results = await Promise.allSettled(
      targets.map((c) =>
        startAudit.mutateAsync({
          cik: c.cik,
          confidence_threshold: confidence,
          yoy_threshold_pct: yoy,
          demo_only: demoOnly,
        }),
      ),
    );
    setBatchProgress(null);
    const started: string[] = [];
    let failed = 0;
    results.forEach((result) => {
      if (result.status === "fulfilled") {
        started.push(result.value.thread_id);
      } else {
        failed += 1;
      }
    });
    if (started.length > 0) setActiveThreadId(started[started.length - 1]);
    if (failed > 0) {
      toast.error(
        `${failed} of ${results.length} audits failed to start. Check SEC EDGAR connectivity and the provider API keys.`,
        { duration: 8000 },
      );
    } else {
      toast.success(`Started ${started.length} audit(s).`);
    }
  };

  const threadStatus = activeStatus.data;
  const canRunSingle = Boolean(chosenCik) && !startAudit.isPending;

  return (
    <div className="space-y-6">
      <div className="grid gap-6 lg:grid-cols-5">
        <Card className="lg:col-span-3">
          <CardHeader>
            <CardTitle>1 · Choose companies</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" aria-hidden="true" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search any company on EDGAR by name or ticker…"
                className="pl-9"
                aria-label="Search companies"
                data-testid="company-search-input"
              />
            </div>
            {mode === "mock" ? (
              <p className="mt-2 text-xs text-muted-foreground">
                Snapshot mode only knows the five preset companies. Set{" "}
                <code className="rounded bg-muted px-1">VITE_API_MODE=http</code> to search live EDGAR and audit any filer.
              </p>
            ) : null}
            {searchResults.data && searchResults.data.length > 0 ? (
              <div className="mt-3 space-y-2" data-testid="company-search-results">
                {searchResults.data.map((company) => {
                  const active = chosenCik === company.cik;
                  return (
                    <button
                      key={company.cik}
                      type="button"
                      onClick={() => chooseCompany(company)}
                      className={cn(
                        "flex w-full items-center justify-between gap-3 rounded-xl border p-3 text-left transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                        active ? "border-primary bg-accent" : "bg-card hover:bg-accent/50"
                      )}
                      data-testid={`search-result-${company.ticker}`}
                    >
                      <span>
                        <span className="block font-medium">{company.name}</span>
                        <span className="block text-xs text-muted-foreground">CIK {company.cik}</span>
                      </span>
                      <span className="flex items-center gap-2">
                        {company.configured ? <Badge variant="secondary">Preset</Badge> : null}
                        <span className="text-sm font-medium text-muted-foreground tabular-nums">{company.ticker}</span>
                      </span>
                    </button>
                  );
                })}
              </div>
            ) : null}

            {isLoading ? <div className="mt-4 h-32 animate-pulse rounded-lg bg-muted" /> : null}
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              {(companies ?? []).map((c) => (
                <button
                  key={c.cik}
                  type="button"
                  onClick={() => toggle(c.cik)}
                  className={cn(
                    "rounded-xl border p-4 text-left transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    selected.includes(c.cik) ? "border-primary bg-accent" : "bg-card hover:bg-accent/50"
                  )}
                  data-testid={`company-card-${c.ticker}`}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-semibold">{c.name}</span>
                    <span className="text-sm font-medium text-muted-foreground tabular-nums">{c.ticker}</span>
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">CIK {c.cik} · {c.industry}</div>
                </button>
              ))}
            </div>

            {chosenCik ? (
              <div className="mt-5 space-y-2">
                <div className="flex items-center justify-between">
                  <div className="text-sm font-medium">Filing</div>
                  {filings.isLoading ? <span className="text-xs text-muted-foreground">Loading filings…</span> : null}
                </div>
                {!filings.isLoading && (filings.data ?? []).length === 0 ? (
                  <p className="text-xs text-muted-foreground" data-testid="filings-empty">
                    No 10-K filings found for CIK {chosenCik}.
                  </p>
                ) : null}
                <div className="space-y-2" data-testid="filing-options">
                  {(filings.data ?? []).map((filing) => {
                    const active = effectiveAccession === filing.accession_number;
                    return (
                      <div key={filing.accession_number} className="flex items-center gap-2">
                        <label
                          className={cn(
                            "flex flex-1 cursor-pointer items-start gap-3 rounded-xl border p-3 transition-all",
                            active ? "border-primary bg-accent" : "hover:bg-accent/50"
                          )}
                        >
                          <input
                            type="radio"
                            name="filing"
                            className="mt-1"
                            checked={active}
                            onChange={() => setAccession(filing.accession_number)}
                          />
                          <span>
                            <span className="block text-sm font-medium">
                              10-K · filed {filing.filing_date || "unknown"}
                            </span>
                            <span className="block text-xs text-muted-foreground">
                              Accession {filing.accession_number}
                              {filing.report_date ? ` · period ${filing.report_date}` : ""}
                            </span>
                          </span>
                        </label>
                        {filing.primary_document ? (
                          <a
                            href={filing.primary_document}
                            target="_blank"
                            rel="noreferrer"
                            className="text-xs font-medium text-primary hover:underline"
                            onClick={(event) => event.stopPropagation()}
                          >
                            Open
                          </a>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
                <p className="text-xs text-muted-foreground">
                  Live mode fetches this filing from SEC EDGAR and scores it fresh; the newest 10-K is used when no filing is selected.
                </p>
              </div>
            ) : null}

            <div className="mt-4 flex flex-wrap items-center gap-2">
              <Button onClick={() => chosenCik && runSingle(chosenCik, effectiveAccession)} disabled={!canRunSingle}>
                <Play className="h-4 w-4" aria-hidden="true" />
                Run audit
              </Button>
              <Button variant="outline" onClick={runAll} disabled={startAudit.isPending}>
                <Layers className="h-4 w-4" aria-hidden="true" />
                Run all companies
              </Button>
              {!chosenCik ? (
                <span className="text-xs text-muted-foreground">
                  {selected.length === 0
                    ? "No company selected — “Run all companies” will audit every supported company."
                    : `${selected.length} companies selected — select exactly one to run a single audit.`}
                </span>
              ) : null}
            </div>
            {batchProgress ? (
              <p className="mt-2 text-xs text-muted-foreground" data-testid="batch-progress">
                Starting {batchProgress.total} audit(s) in parallel…
              </p>
            ) : null}
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>2 · Policy thresholds</CardTitle>
          </CardHeader>
          <CardContent className="space-y-6">
            <div>
              <div className="flex items-center justify-between">
                <Label htmlFor="confidence-threshold">Confidence threshold</Label>
                <span className="rounded-full bg-accent px-2 py-0.5 text-sm font-semibold tabular-nums">{(confidence * 100).toFixed(0)}%</span>
              </div>
              <Slider id="confidence-threshold" min={0.5} max={0.98} step={0.01} value={[confidence]} onValueChange={(v) => setConfidence(v[0])} />
              <p className="mt-1 text-xs text-muted-foreground">Metrics below this confidence escalate for human review.</p>
            </div>
            <div>
              <div className="flex items-center justify-between">
                <Label htmlFor="yoy-threshold">YoY change tolerance</Label>
                <span className="rounded-full bg-accent px-2 py-0.5 text-sm font-semibold tabular-nums">{(yoy * 100).toFixed(0)}%</span>
              </div>
              <Slider id="yoy-threshold" min={0} max={0.8} step={0.01} value={[yoy]} onValueChange={(v) => setYoY(v[0])} />
              <p className="mt-1 text-xs text-muted-foreground">Larger year-over-year deviations escalate for financial anomaly review.</p>
            </div>
            <div className="flex items-center justify-between rounded-xl border p-4">
              <div>
                <div className="text-sm font-medium">Mark as test run</div>
                <div className="text-xs text-muted-foreground">
                  Tags this audit as a test run. Test runs are excluded from the headline accuracy and escalation metrics
                  so they never distort the official numbers.
                </div>
              </div>
              <Switch checked={demoOnly} onCheckedChange={setDemoOnly} aria-label="Mark as test run" />
            </div>
          </CardContent>
        </Card>
      </div>

      {activeThreadId ? (
        <Card>
          <CardHeader>
            <CardTitle>Run status</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center gap-3">
              {threadStatus ? <StatusBadge status={threadStatus.status} /> : <Badge variant="secondary">Starting…</Badge>}
              {threadStatus?.demo_only ? <Badge variant="outline">Preset</Badge> : null}
            </div>
            <WorkflowStepper status={threadStatus?.status ?? "STARTED"} />
            {threadStatus?.status === "PAUSED_FOR_REVIEW" ? (
              <div className="flex items-center gap-3 rounded-xl border border-warning/40 bg-warning/10 p-4">
                <Badge variant="warning">Paused at checkpoint</Badge>
                <span className="text-sm">This audit is waiting for human decisions.</span>
                <Button size="sm" className="ml-auto" onClick={() => navigate(`/review?thread=${activeThreadId}`)}>
                  Open review queue
                </Button>
              </div>
            ) : null}
            {threadStatus?.status === "COMPLETED" || threadStatus?.status === "AUTO_APPROVED" ? (
              <div className="flex items-center gap-3 rounded-xl border border-success/40 bg-success/10 p-4">
                <Badge variant="success">{threadStatus.status === "COMPLETED" ? "Completed" : "Auto-approved"}</Badge>
                <span className="text-sm">This audit finished without needing a human decision.</span>
                <Button size="sm" className="ml-auto" onClick={() => navigate(`/filings/${activeThreadId}`)}>
                  Open filing detail
                </Button>
              </div>
            ) : null}
            {threadStatus?.status === "FAILED" ? (
              <div className="rounded-xl border border-danger/40 bg-danger/10 p-4 text-sm text-danger">
                This audit failed. Check that SEC EDGAR is reachable and that the Gemini and Groq API keys in
                <code className="mx-1 rounded bg-muted px-1">backend/.env</code> are valid. The simulate-failure switch
                in Settings can also force this state.
              </div>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      <p className="text-xs text-muted-foreground">Live 9-step workflow · Filing retrieval → Extraction → Confidence scoring → Validation → Automated decision → Human review → Decision and resume → XBRL verification → Audit history</p>
    </div>
  );
}
