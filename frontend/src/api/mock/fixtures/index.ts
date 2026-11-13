import type {
  AnomalyExplanation,
  ConfidenceSignals,
  EscalationType,
  FlaggedItem,
  Metric,
  Severity,
} from "../../types";
import secSnapshot from "./sec-snapshot.json";

export interface CompanyFixture {
  name: string;
  ticker: string;
  cik: string;
  industry: string;
  fiscal_year: number;
  period_end: string;
  fiscalYearEnd?: string;
  filing_url: string;
  metrics: Metric[];
  flaggedItems: FlaggedItem[];
  xbrl: Record<string, number>;
  isPlaceholder?: boolean;
  isDemoScenario?: boolean;
  accession_number?: string;
  filed_date?: string;
  filing_index_url?: string;
  secMetrics?: any;
  retrieved_at?: string;
}

function makeSignals(overrides: Partial<ConfidenceSignals> = {}): ConfidenceSignals {
  return {
    snippet_grounding: 0.9,
    concept_match: 0.93,
    unit_consistency: 0.95,
    period_consistency: 0.97,
    prior_year_sanity: 0.9,
    extractor_agreement: 0.9,
    column_period: 0.9,
    cross_statement: 0.9,
    llm_confidence: null,
    ...overrides,
  };
}

function makeMetric(seed: any): Metric {
  const confidenceLabel = seed.confidence >= 0.85 ? "HIGH" : "LOW";
  return {
    metric_name: seed.metric_name,
    value_usd: seed.value_usd,
    original_value: seed.original_value ?? seed.value_usd,
    unit: seed.unit ?? "USD",
    period: seed.period ?? "FY",
    prior_year_value_usd: seed.prior_year_value_usd ?? null,
    confidence: seed.confidence ?? 0.95,
    confidence_label: confidenceLabel,
    confidence_signals: makeSignals(seed.confidence_signals || {}),
    source_snippet: seed.source_snippet ?? "",
    source_header: seed.source_header ?? "",
    source_row: seed.source_row ?? "",
    decision_state: seed.decision_state ?? "PENDING",
  } as Metric;
}

function flag(metric: Metric, severity: Severity, escalationTypes: EscalationType[], reasons: string[], rules: string[]): FlaggedItem {
  return {
    ...metric,
    decision_state: "FLAGGED",
    reasons,
    rules,
    severity,
    escalation_types: escalationTypes,
  };
}

const INDUSTRY_MAP: Record<string, string> = {
  AAPL: "Technology",
  MSFT: "Technology",
  NVDA: "Semiconductors",
  TSLA: "Consumer Discretionary",
  AMZN: "Consumer Discretionary",
};

function buildCompanies(): CompanyFixture[] {
  const companies: CompanyFixture[] = [];
  const snapshot: any = secSnapshot;
  const retrieved_at = snapshot.retrieved_at;
  
  // Use TSLA as demo scenario with injected error
  const demoTicker = "TSLA";
  
  for (const ticker of ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN"]) {
    const c = snapshot.companies[ticker];
    if (!c) continue;
    const metricsArr: Metric[] = [];
    const xbrl: Record<string, number> = {};
    const metricOrder = ['Revenue', 'Net Income', 'Total Assets', 'Total Liabilities', "Shareholders' Equity", 'Operating Cash Flow', 'Cash and Cash Equivalents', 'R&D Expense'];
    
    for (const name of metricOrder) {
      const m = c.metrics[name];
      if (!m) continue;
      let value = m.value_usd;
      // Inject error in TSLA for one metric (Revenue) - drop a digit
      if (ticker === demoTicker && name === 'Revenue') {
        value = Math.floor(m.value_usd / 10); // perturb
      }
      metricsArr.push(makeMetric({
        metric_name: name,
        value_usd: value,
        original_value: value,
        prior_year_value_usd: m.prior_year_value_usd,
        confidence: ticker === demoTicker && name === 'Revenue' ? 0.72 : 0.95,
        period: `FY${c.fiscal_year}`,
      }));
      xbrl[name === 'R&D Expense' ? 'Research and Development' : name] = m.value_usd; // XBRL true value is unmodified
    }
    
    const flaggedItems: FlaggedItem[] = [];
    if (ticker === demoTicker) {
      // Find revenue metric
      const revMetric = metricsArr.find(m => m.metric_name === 'Revenue');
      if (revMetric) {
        const trueRev = c.metrics['Revenue']?.value_usd || revMetric.value_usd;
        const priorRev = revMetric.prior_year_value_usd || c.metrics['Revenue']?.prior_year_value_usd;
        let yoyStr = 'n/a';
        if (priorRev && priorRev !== 0) {
          const yoy = ((trueRev - priorRev) / Math.abs(priorRev)) * 100;
          yoyStr = `${yoy >= 0 ? '+' : ''}${yoy.toFixed(2)}%`;
        }
        flaggedItems.push(flag(
          { ...revMetric, decision_state: 'PENDING' } as Metric,
          'HIGH',
          ['EXTRACTION_RISK'],
          [
            `Extracted value ${revMetric.value_usd.toLocaleString('en-US')} vs XBRL ${trueRev.toLocaleString('en-US')}`,
            `Prior year ${priorRev != null ? priorRev.toLocaleString('en-US') : 'n/a'}, YoY ${yoyStr}`,
            'Rule: XBRL_MISMATCH (threshold: exact match)',
          ],
          ['INJECTED_ERROR', 'XBRL_MISMATCH']
        ));
      }
    }
    
    companies.push({
      name: c.name,
      ticker: c.ticker,
      cik: c.cik,
      industry: INDUSTRY_MAP[c.ticker] || 'Technology',
      fiscal_year: c.fiscal_year,
      period_end: c.period_end,
      fiscalYearEnd: c.fiscalYearEnd,
      filing_url: c.filing_index_url,
      metrics: metricsArr,
      flaggedItems,
      xbrl: ticker === demoTicker ? Object.fromEntries(Object.entries(xbrl).map(([k,v]) => [k === 'R&D Expense' ? 'Research and Development' : k, c.metrics[k === 'Research and Development' ? 'R&D Expense' : k]?.value_usd || v])) : xbrl,
      isPlaceholder: false,
      isDemoScenario: ticker === demoTicker,
      accession_number: c.accession_number,
      filed_date: c.filed_date,
      filing_index_url: c.filing_index_url,
      secMetrics: c.metrics,
      retrieved_at,
    } as CompanyFixture);
  }
  
  return companies;
}

export const FIXTURES = {
  companies: buildCompanies(),
  retrieved_at: (secSnapshot as any).retrieved_at,
};

export function findCompanyByCik(cik: string): CompanyFixture {
  const match = FIXTURES.companies.find((c) => c.cik === cik);
  if (!match) throw new Error(`Unknown company ${cik}`);
  return match;
}

export function trueValueFor(company: CompanyFixture, metricName: string): number {
  if (metricName === 'Research and Development') {
    return company.xbrl['Research and Development'] ?? company.xbrl['R&D Expense'] ?? 0;
  }
  return company.xbrl[metricName] ?? 0;
}

export function flaggedForCompany(company: CompanyFixture): FlaggedItem[] {
  return company.flaggedItems;
}

export function buildAnomalyExplanation(metric: FlaggedItem): AnomalyExplanation {
  const isExtraction = metric.escalation_types.includes("EXTRACTION_RISK");
  return {
    metric_name: metric.metric_name,
    title: metric.rules?.includes('INJECTED_ERROR') ? "Injected error" : (isExtraction ? "Extraction risk" : "Financial anomaly"),
    detail: metric.reasons.join("; "),
    severity: metric.severity,
  };
}

export { secSnapshot };
