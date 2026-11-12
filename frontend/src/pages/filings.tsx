import { useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useThreads } from "@/hooks/use-trace";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatPercent, formatDateTime } from "@/lib/format";

export function FilingsPage() {
  const navigate = useNavigate();
  const { data: threads, isLoading, error, refetch, isFetching } = useThreads();
  const [searchParams] = useSearchParams();
  const q = (searchParams.get("search") ?? "").trim().toLowerCase();

  const rows = useMemo(() => {
    const list = threads ?? [];
    const filtered = q
      ? list.filter(
          (t) =>
            t.company_name.toLowerCase().includes(q) ||
            t.ticker.toLowerCase().includes(q) ||
            t.cik.includes(q),
        )
      : list;
    return [...filtered].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  }, [threads, q]);

  if (isLoading) {
    return <div className="space-y-4">Loading filings…</div>;
  }

  if (error || !threads) {
    return (
      <div className="rounded-xl border p-6 text-sm">
        Failed to load filings.
        <Button variant="outline" size="sm" className="ml-3" onClick={() => void refetch()}>
          Retry
        </Button>
      </div>
    );
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <CardTitle>Filings</CardTitle>
          <div className="flex items-center gap-2">
            {q ? (
              <span className="text-sm text-muted-foreground">
                {rows.length} of {(threads ?? []).length} audits match “{searchParams.get("search")}”
              </span>
            ) : null}
            <Button
              variant="outline"
              size="sm"
              onClick={() => void refetch()}
              disabled={isFetching}
              data-testid="filings-refresh"
            >
              {isFetching ? "Refreshing…" : "Refresh"}
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
              <th className="py-2 pr-4 font-medium">Company</th>
              <th className="py-2 pr-4 font-medium">Ticker</th>
              <th className="py-2 pr-4 font-medium">CIK</th>
              <th className="py-2 pr-4 font-medium">Fiscal year</th>
              <th className="py-2 pr-4 font-medium">Status</th>
              <th className="py-2 pr-4 font-medium">Flagged</th>
              <th className="py-2 pr-4 font-medium">Auto-approved</th>
              <th className="py-2 pr-4 font-medium">Accuracy</th>
              <th className="py-2 pr-4 font-medium">Updated</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => (
              <tr
                key={t.thread_id}
                className="cursor-pointer border-b last:border-0 hover:bg-accent/40"
                onClick={() => navigate(`/filings/${t.thread_id}`)}
                data-testid={`filing-row-${t.thread_id}`}
                title="Open filing detail"
              >
                <td className="py-2.5 pr-4 font-medium">{t.company_name}</td>
                <td className="py-2.5 pr-4 tabular-nums">{t.ticker}</td>
                <td className="py-2.5 pr-4 tabular-nums">{t.cik}</td>
                <td className="py-2.5 pr-4 tabular-nums">{t.fiscal_year ?? "—"}</td>
                <td className="py-2.5 pr-4">
                  <StatusBadge status={t.status} />
                </td>
                <td className="py-2.5 pr-4 tabular-nums">{t.flagged_count}</td>
                <td className="py-2.5 pr-4 tabular-nums">{t.auto_approved_count}</td>
                <td className="py-2.5 pr-4 tabular-nums">
                  {t.accuracy_pct == null ? "—" : formatPercent(t.accuracy_pct, 1)}
                </td>
                <td className="py-2.5 pr-4 text-muted-foreground">{formatDateTime(t.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 ? (
          <div className="py-6 text-center text-sm text-muted-foreground">
            {q
              ? `No filings match “${searchParams.get("search")}”. Clear the search to see every audit.`
              : "No audits yet. Start one from the New audit page."}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
