import { useState } from "react";
import { useParams } from "react-router-dom";
import { Copy, Check, AlertTriangle } from "lucide-react";
import { useThreadStatus, useThreadTimeline, useDataMode } from "@/hooks/use-trace";
import { StatusBadge, ConfidenceBadge, DecisionStateBadge, EscalationBadge, SeverityBadge } from "@/components/status-badge";
import { ConfidenceBar } from "@/components/confidence-bar";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { formatUsdFull, formatUsdCompactPrecise, formatPercent, formatDateTime } from "@/lib/format";
import type { AuditStatusValue } from "@/api/types";
import { secSnapshot } from "@/api/mock/fixtures";

export function FilingDetailPage() {
  // The route is declared as "filings/:id".
  const { id: threadId = "" } = useParams();
  const { data: status, error, isLoading, refetch } = useThreadStatus(threadId || null);
  const { data: timeline } = useThreadTimeline(threadId || null);
  const { mode } = useDataMode();
  const [tab, setTab] = useState("metrics");
  const [expandedMetric, setExpandedMetric] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  if (isLoading) {
    return <div className="space-y-4">Loading filing…</div>;
  }
  if (error || !status) {
    return (
      <div className="rounded-xl border p-6 text-sm">
        Failed to load filing.{" "}
        <Button variant="outline" size="sm" onClick={() => void refetch()}>
          Retry
        </Button>
      </div>
    );
  }

  const copyThreadId = async () => {
    try {
      await navigator.clipboard.writeText(status.thread_id);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  const accuracy = status.accuracy_report;

  const validationGroups = new Map<string, typeof status.validation_checks>();
  for (const check of status.validation_checks) {
    const list = validationGroups.get(check.metric_name) ?? [];
    list.push(check);
    validationGroups.set(check.metric_name, list);
  }
  const validationRows = [...validationGroups.entries()];
  const validationCounts = status.validation_checks.reduce(
    (acc, c) => {
      acc[c.status] = (acc[c.status] ?? 0) + 1;
      return acc;
    },
    { PASSED: 0, FAILED: 0, SKIPPED: 0 } as Record<string, number>
  );

  return (
    <div className="space-y-6">
      <Card>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-xl font-semibold">{status.company_name}</h2>
                <StatusBadge status={status.status as AuditStatusValue} />
              </div>
              <div className="mt-1 text-sm text-muted-foreground">
                Ticker {status.ticker} · CIK {status.cik} · FY{status.fiscal_year} · Period end {status.period_end}
              </div>
              <div className="mt-2 flex items-center gap-2">
                <code className="rounded bg-muted px-2 py-1 text-xs" data-testid="thread-id">{status.thread_id}</code>
                <Button variant="outline" size="sm" onClick={copyThreadId}>
                  {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                  {copied ? "Copied" : "Copy"}
                </Button>
                {status.filing_url ? (
                  <a className="text-sm text-info hover:underline" href={status.filing_url} target="_blank" rel="noreferrer">
                    View filing ↗
                  </a>
                ) : (
                  <span className="text-sm text-muted-foreground">No source filing URL recorded</span>
                )}
              </div>
            </div>
            {status.accuracy_report ? <div className="text-right">Accuracy {formatPercent(status.accuracy_report.accuracy_pct, 1)}</div> : null}
          </div>
          {status.demo_only ? <Badge variant="warning">Preset</Badge> : null}
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList>
              <TabsTrigger value="metrics">Metrics</TabsTrigger>
              <TabsTrigger value="validation">Validation</TabsTrigger>
              <TabsTrigger value="workflow">Workflow</TabsTrigger>
              <TabsTrigger value="accuracy">Accuracy</TabsTrigger>
            </TabsList>

            <TabsContent value="metrics" className="mt-4">
              <div className="space-y-2">
                {(status.flagged_items ?? []).length > 0 ? (
                  <div className="space-y-2" data-testid="flagged-summary">
                    {(status.flagged_items ?? []).map((f) => (
                      <div key={f.metric_name} className="rounded-xl border border-danger/40 bg-danger/5 p-3">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-semibold">{f.metric_name}</span>
                          <SeverityBadge severity={f.severity} />
                          {f.escalation_types.map((t) => (
                            <EscalationBadge key={t} type={t} />
                          ))}
                        </div>
                        <ul className="mt-2 space-y-1">
                          {f.reasons.map((reason, index) => (
                            <li key={index} className="flex items-start gap-2 text-sm">
                              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-danger" aria-hidden="true" />
                              <span>{reason}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    ))}
                  </div>
                ) : null}
                <div className="overflow-x-auto rounded-xl border">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b bg-muted/30 text-left text-xs uppercase tracking-wide text-muted-foreground">
                        <th className="py-2 px-3 font-medium">Metric</th>
                        <th className="py-2 px-3 font-medium text-right">Current</th>
                        <th className="py-2 px-3 font-medium text-right">Prior year</th>
                        <th className="py-2 px-3 font-medium text-right">YoY %</th>
                        <th className="py-2 px-3 font-medium">Confidence</th>
                        <th className="py-2 px-3 font-medium">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(status.metrics ?? []).map((m) => {
                        const prior = m.prior_year_value_usd;
                        let yoy: number | null = null;
                        if (prior && prior !== 0) {
                          yoy = ((m.value_usd - prior) / Math.abs(prior)) * 100;
                        }
                        const expanded = expandedMetric === m.metric_name;
                        return (
                          <>
                            <tr
                              key={m.metric_name}
                              className="border-b cursor-pointer hover:bg-muted/20"
                              onClick={() => setExpandedMetric(expanded ? null : m.metric_name)}
                              aria-expanded={expanded}
                            >
                              <td className="py-2.5 px-3">
                                <div className="font-medium">{m.metric_name}</div>
                                <div className="text-xs text-muted-foreground">{m.period}</div>
                              </td>
                              <td className="py-2.5 px-3 text-right">
                                <div className="font-semibold tabular-nums">{formatUsdFull(m.value_usd)}</div>
                                <div className="text-xs text-muted-foreground tabular-nums">{formatUsdCompactPrecise(m.value_usd)}</div>
                              </td>
                              <td className="py-2.5 px-3 text-right tabular-nums">
                                {prior != null ? (
                                  <>
                                    <div>{formatUsdFull(prior)}</div>
                                    <div className="text-xs text-muted-foreground">{formatUsdCompactPrecise(prior)}</div>
                                  </>
                                ) : (
                                  "—"
                                )}
                              </td>
                              <td className="py-2.5 px-3 text-right tabular-nums">{yoy == null ? "—" : `${yoy >= 0 ? "+" : ""}${yoy.toFixed(2)}%`}</td>
                              <td className="py-2.5 px-3">
                                <ConfidenceBadge confidence={m.confidence} label={m.confidence_label} />
                              </td>
                              <td className="py-2.5 px-3">
                                <DecisionStateBadge state={m.decision_state} />
                              </td>
                            </tr>
                            {expanded ? (
                              <tr key={`${m.metric_name}-expanded`} className="border-b bg-muted/10">
                                <td colSpan={6} className="p-3">
                                  <div className="space-y-2">
                                    <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Source evidence</div>
                                    {mode === "http" && m.source_header ? (
                                      <>
                                        <div className="rounded-lg border bg-background p-2">
                                          <div className="text-xs text-muted-foreground">Header row</div>
                                          <div className="mt-1 font-mono text-xs">{m.source_header || "—"}</div>
                                        </div>
                                        <div className="rounded-lg border bg-background p-2 ring-1 ring-info/40">
                                          <div className="text-xs text-muted-foreground">Highlighted source row</div>
                                          <div className="mt-1 font-mono text-xs break-words">{m.source_row || m.source_snippet || "—"}</div>
                                        </div>
                                        <div className="font-mono text-xs break-words text-muted-foreground">{m.source_snippet}</div>
                                      </>
                                    ) : (
                                      <>
                                        {(() => {
                                          const snap = (secSnapshot as any)?.companies?.[status.ticker];
                                          const sm = snap?.metrics?.[m.metric_name === 'Research and Development' ? 'R&D Expense' : m.metric_name];
                                          const tag = sm?.xbrl_tag || 'N/A';
                                          const accn = snap?.accession_number || status.cik;
                                          const pstart = sm?.period_start;
                                          const pend = sm?.period_end || status.period_end;
                                          const furl = snap?.filing_index_url || status.filing_url;
                                          const periodStr = pstart ? `${pstart} to ${pend}` : pend;
                                          return (
                                            <div className="rounded-lg border bg-background p-2">
                                              <div className="font-mono text-xs">XBRL fact: us-gaap:{tag} | period {periodStr} | accession {accn}</div>
                                              {furl ? (
                                                <a className="mt-1 inline-block text-xs text-info hover:underline" href={furl} target="_blank" rel="noreferrer">
                                                  Filing index ↗
                                                </a>
                                              ) : null}
                                            </div>
                                          );
                                        })()}
                                      </>
                                    )}
                                    <ConfidenceBar value={m.confidence} label={m.confidence_label} signals={m.confidence_signals} id={m.metric_name} />
                                  </div>
                                </td>
                              </tr>
                            ) : null}
                          </>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            </TabsContent>

            <TabsContent value="validation" className="mt-4">
              <div className="grid gap-4 sm:grid-cols-3">
                <div className="rounded-xl border p-4">
                  <div className="text-xs uppercase tracking-wide text-muted-foreground">Passed</div>
                  <div className="mt-1 text-2xl font-semibold text-success">{validationCounts.PASSED ?? 0}</div>
                </div>
                <div className="rounded-xl border p-4">
                  <div className="text-xs uppercase tracking-wide text-muted-foreground">Failed</div>
                  <div className="mt-1 text-2xl font-semibold text-danger">{validationCounts.FAILED ?? 0}</div>
                </div>
                <div className="rounded-xl border p-4">
                  <div className="text-xs uppercase tracking-wide text-muted-foreground">Skipped</div>
                  <div className="mt-1 text-2xl font-semibold text-muted-foreground">{validationCounts.SKIPPED ?? 0}</div>
                </div>
              </div>
              <div className="mt-4 space-y-2">
                {validationRows.map(([metric, checks]) => (
                  <div key={metric} className="rounded-xl border p-3">
                    <div className="font-medium">{metric}</div>
                    {checks.map((c) => (
                      <div key={c.rule_name} className="mt-2 rounded-lg border p-2 text-sm">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-mono text-xs">{c.rule_name}</span>
                          <Badge variant={c.status === "PASSED" ? "success" : c.status === "FAILED" ? "danger" : "muted"}>{c.status}</Badge>
                        </div>
                        <div className="mt-1 text-muted-foreground">{c.reason}</div>
                        {c.details ? <div className="mt-1 text-xs text-muted-foreground">{c.details}</div> : null}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            </TabsContent>

            <TabsContent value="workflow" className="mt-4">
              <ol className="relative space-y-4 border-l border-border pl-6">
                {(timeline ?? []).map((e) => (
                  <li key={e.id} className="relative">
                    <span className="absolute -left-[31px] h-2.5 w-2.5 rounded-full bg-info" aria-hidden="true" />
                    <div className="text-sm font-medium">{e.event}</div>
                    <div className="text-xs text-muted-foreground">
                      Step {e.step} · {formatDateTime(e.timestamp)}
                    </div>
                    {e.detail ? <div className="text-xs text-muted-foreground">{e.detail}</div> : null}
                  </li>
                ))}
              </ol>
            </TabsContent>

            <TabsContent value="accuracy" className="mt-4">
              {accuracy ? (
                <div className="space-y-4">
                  <div className="grid gap-4 sm:grid-cols-4">
                    <div className="rounded-xl border p-4">
                      <div className="text-xs uppercase tracking-wide text-muted-foreground">Accuracy</div>
                      <div className="mt-1 text-2xl font-semibold">{formatPercent(accuracy.accuracy_pct, 1)}</div>
                    </div>
                    <div className="rounded-xl border p-4">
                      <div className="text-xs uppercase tracking-wide text-muted-foreground">Original accuracy</div>
                      <div className="mt-1 text-2xl font-semibold">{formatPercent(accuracy.original_accuracy_pct, 1)}</div>
                    </div>
                    <div className="rounded-xl border p-4">
                      <div className="text-xs uppercase tracking-wide text-muted-foreground">False auto-approvals</div>
                      <div className="mt-1 text-2xl font-semibold">{accuracy.false_auto_approvals ?? 0}</div>
                    </div>
                    <div className="rounded-xl border p-4">
                      <div className="text-xs uppercase tracking-wide text-muted-foreground">Escalation precision</div>
                      <div className="mt-1 text-2xl font-semibold">{formatPercent(accuracy.escalation_precision_pct, 1)}</div>
                    </div>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
                          <th className="py-2 pr-4 font-medium">Metric</th>
                          <th className="py-2 pr-4 font-medium">Extracted</th>
                          <th className="py-2 pr-4 font-medium">Final</th>
                          <th className="py-2 pr-4 font-medium">XBRL</th>
                          <th className="py-2 pr-4 font-medium">Match</th>
                          <th className="py-2 pr-4 font-medium">Diff %</th>
                        </tr>
                      </thead>
                      <tbody>
                        {accuracy.metric_details.map((d) => (
                          <tr key={d.metric} className="border-b last:border-0">
                            <td className="py-2.5 pr-4 font-medium">{d.metric}</td>
                            <td className="py-2.5 pr-4 tabular-nums">{formatUsdFull(d.extracted)}</td>
                            <td className="py-2.5 pr-4 tabular-nums">{formatUsdFull(d.final)}</td>
                            <td className="py-2.5 pr-4 tabular-nums">{formatUsdFull(d.xbrl)}</td>
                            <td className="py-2.5 pr-4">
                              {d.match == null ? (
                                <Badge variant="outline">No ground truth</Badge>
                              ) : d.match ? (
                                <Badge variant="success">Match</Badge>
                              ) : (
                                <Badge variant="danger">Mismatch</Badge>
                              )}
                            </td>
                            <td className="py-2.5 pr-4 tabular-nums">{d.diff_pct == null ? "—" : `${d.diff_pct.toFixed(2)}%`}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">Accuracy report not available for this filing.</p>
              )}
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>
    </div>
  );
}
