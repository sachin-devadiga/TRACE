import type {
  AnomalyExplanation,
  ConfidenceSignals,
  DecisionState,
  EscalationType,
  FlaggedItem,
  Metric,
  Severity,
} from "../types";

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

interface MetricSeed {
  metric_name: string;
  value_usd: number;
  prior_year_value_usd: number | null;
  confidence: number;
  unit?: string;
  period?: string;
  source_snippet?: string;
  source_header?: string;
  source_row?: string;
  decision_state?: DecisionState;
  severity?: Severity;
  escalation_types?: EscalationType[];
  reasons?: string[];
  rules?: string[];
}

function makeMetric(seed: MetricSeed): Metric {
  const confidenceLabel = seed.confidence >= 0.85 ? "HIGH" : "LOW";
  const compact =
    seed.value_usd >= 1e9
      ? `$${(seed.value_usd / 1e9).toFixed(2)}B`
      : seed.value_usd >= 1e6
        ? `$${(seed.value_usd / 1e6).toFixed(2)}M`
        : `$${seed.value_usd.toFixed(2)}`;
  return {
    metric_name: seed.metric_name,
    value_usd: seed.value_usd,
    original_value: seed.value_usd,
    unit: seed.unit ?? "USD",
    period: seed.period ?? "FY",
    prior_year_value_usd: seed.prior_year_value_usd,
    confidence: seed.confidence,
    confidence_label: confidenceLabel,
    confidence_signals: makeSignals(),
    source_snippet: seed.source_snippet ?? `${seed.metric_name} from consolidated statements — ${compact} (placeholder fixture)`,
    source_header: seed.source_header ?? "Consolidated Statements",
    source_row: seed.source_row ?? "FY",
    decision_state: seed.decision_state ?? "PENDING",
  };
}

function flag(
  metric: Metric,
  severity: Severity,
  escalationTypes: EscalationType[],
  reasons: string[],
  rules: string[]
): FlaggedItem {
  return {
    ...metric,
    decision_state: "FLAGGED",
    reasons,
    rules,
    severity,
    escalation_types: escalationTypes,
  };
}

export const FIXTURES = {
  companies: [
    {
      name: "Apple Inc.",
      ticker: "AAPL",
      cik: "0000320193",
      industry: "Technology",
      fiscal_year: 2025,
      period_end: "2025-09-27", // last Saturday of September 2025 (placeholder fixture)
      fiscalYearEnd: "09-27", // last Saturday of September
      filing_url: "https://www.sec.gov/Archives/edgar/data/320193/000032019325000106/aapl-20250927.htm",
      isPlaceholder: true,
      metrics: [
        makeMetric({
          metric_name: "Revenue",
          value_usd: 434600000000,
          prior_year_value_usd: 383300000000,
          confidence: 0.78,
          unit: "USD",
          period: "FY2025",
          source_snippet: "Revenue from Contracts with Customers — $434.60B (placeholder fixture)",
          source_header: "Consolidated Statements of Operations",
          source_row: "Net sales",
          decision_state: "FLAGGED",
        }),
        makeMetric({ metric_name: "Net Income", value_usd: 96700000000, prior_year_value_usd: 93700000000, confidence: 0.93, period: "FY2025" }),
        makeMetric({ metric_name: "Total Assets", value_usd: 352800000000, prior_year_value_usd: 364980000000, confidence: 0.94, period: "FY2025" }),
        makeMetric({ metric_name: "Total Liabilities", value_usd: 287000000000, prior_year_value_usd: 290000000000, confidence: 0.93, period: "FY2025" }),
        makeMetric({ metric_name: "Shareholders' Equity", value_usd: 65800000000, prior_year_value_usd: 74980000000, confidence: 0.92, period: "FY2025" }),
        makeMetric({ metric_name: "Operating Cash Flow", value_usd: 122000000000, prior_year_value_usd: 110500000000, confidence: 0.91, period: "FY2025" }),
        makeMetric({ metric_name: "Cash and Cash Equivalents", value_usd: 29900000000, prior_year_value_usd: 30700000000, confidence: 0.9, period: "FY2025" }),
        makeMetric({ metric_name: "Research and Development", value_usd: 30300000000, prior_year_value_usd: 29900000000, confidence: 0.92, period: "FY2025" }),
      ],
      flaggedItems: [
        flag(
          makeMetric({
            metric_name: "Revenue",
            value_usd: 434600000000,
            prior_year_value_usd: 383300000000,
            confidence: 0.78,
            unit: "USD",
            period: "FY2025",
            source_snippet: "Revenue from Contracts with Customers — $434.60B (placeholder fixture)",
            source_header: "Consolidated Statements of Operations",
            source_row: "Net sales",
            decision_state: "FLAGGED",
          }),
          "HIGH",
          ["EXTRACTION_RISK"],
          ["Revenue concept resolved via secondary statement", "Low snippet grounding for revenue recognition boundary"],
          ["CONCEPT_SYNONYM", "SNIPPET_BOUNDARY"]
        ),
        flag(
          makeMetric({
            metric_name: "Net Income",
            value_usd: 96700000000,
            prior_year_value_usd: 93700000000,
            confidence: 0.81,
            unit: "USD",
            period: "FY2025",
            source_snippet: "Net income — $96.70B (placeholder fixture)",
            source_header: "Consolidated Statements of Operations",
            source_row: "Net income",
            decision_state: "FLAGGED",
          }),
          "HIGH",
          ["FINANCIAL_ANOMALY"],
          ["Net income margin deviation exceeds tolerance", "Year-over-year change requires review"],
          ["YOY_TOLERANCE", "MARGIN_ANOMALY"]
        ),
        flag(
          makeMetric({
            metric_name: "Total Assets",
            value_usd: 352800000000,
            prior_year_value_usd: 364980000000,
            confidence: 0.82,
            unit: "USD",
            period: "FY2025",
            source_snippet: "Total assets — $352.80B (placeholder fixture)",
            source_header: "Consolidated Balance Sheets",
            source_row: "Total assets",
            decision_state: "FLAGGED",
          }),
          "MEDIUM",
          ["EXTRACTION_RISK"],
          ["Balance sheet classification requires verification"],
          ["STATEMENT_CROSSCHECK"]
        ),
      ],
      xbrl: {
        Revenue: 436200000000,
        "Net Income": 112800000000,
        "Total Assets": 396400000000,
        "Total Liabilities": 304400000000,
        "Shareholders' Equity": 92600000000,
        "Operating Cash Flow": 118300000000,
        "Cash and Cash Equivalents": 61100000000,
        "Research and Development": 31200000000,
      },
    },
    {
      name: "Microsoft Corporation",
      ticker: "MSFT",
      cik: "0000789019",
      industry: "Technology",
      fiscal_year: 2025,
      period_end: "2025-06-30",
      fiscalYearEnd: "06-30",
      filing_url: "https://www.sec.gov/Archives/edgar/data/789019/000078901925000106/msft-20250630.htm",
      isPlaceholder: true,
      metrics: [
        makeMetric({ metric_name: "Revenue", value_usd: 254600000000, prior_year_value_usd: 236900000000, confidence: 0.96, period: "FY2025" }),
        makeMetric({ metric_name: "Net Income", value_usd: 102200000000, prior_year_value_usd: 88000000000, confidence: 0.94, period: "FY2025" }),
        makeMetric({ metric_name: "Total Assets", value_usd: 571000000000, prior_year_value_usd: 562000000000, confidence: 0.93, period: "FY2025" }),
        makeMetric({ metric_name: "Total Liabilities", value_usd: 489000000000, prior_year_value_usd: 470000000000, confidence: 0.9, period: "FY2025" }),
        makeMetric({ metric_name: "Shareholders' Equity", value_usd: 82000000000, prior_year_value_usd: 77000000000, confidence: 0.88, period: "FY2025" }),
        makeMetric({ metric_name: "Operating Cash Flow", value_usd: 99800000000, prior_year_value_usd: 94200000000, confidence: 0.9, period: "FY2025" }),
        makeMetric({ metric_name: "Cash and Cash Equivalents", value_usd: 30500000000, prior_year_value_usd: 29500000000, confidence: 0.89, period: "FY2025" }),
        makeMetric({ metric_name: "Research and Development", value_usd: 30000000000, prior_year_value_usd: 28000000000, confidence: 0.95, period: "FY2025" }),
      ],
      flaggedItems: [],
      xbrl: {
        Revenue: 254600000000,
        "Net Income": 102200000000,
        "Total Assets": 571000000000,
        "Total Liabilities": 489000000000,
        "Shareholders' Equity": 82000000000,
        "Operating Cash Flow": 99800000000,
        "Cash and Cash Equivalents": 30500000000,
        "Research and Development": 30000000000,
      },
    },
    {
      name: "NVIDIA Corporation",
      ticker: "NVDA",
      cik: "0001045810",
      industry: "Semiconductors",
      fiscal_year: 2025,
      period_end: "2025-01-26", // last Sunday of January 2025 (placeholder fixture)
      fiscalYearEnd: "01-26",
      filing_url: "https://www.sec.gov/Archives/edgar/data/1045810/000104581025000106/nvidia-20250126.htm",
      isPlaceholder: true,
      metrics: [
        makeMetric({ metric_name: "Revenue", value_usd: 68000000000, prior_year_value_usd: 60000000000, confidence: 0.97, period: "FY2025" }),
        makeMetric({ metric_name: "Net Income", value_usd: 26700000000, prior_year_value_usd: 19600000000, confidence: 0.98, period: "FY2025" }),
        makeMetric({ metric_name: "Total Assets", value_usd: 104000000000, prior_year_value_usd: 98100000000, confidence: 0.97, period: "FY2025" }),
        makeMetric({ metric_name: "Total Liabilities", value_usd: 33000000000, prior_year_value_usd: 31500000000, confidence: 0.96, period: "FY2025" }),
        makeMetric({ metric_name: "Shareholders' Equity", value_usd: 71000000000, prior_year_value_usd: 67100000000, confidence: 0.97, period: "FY2025" }),
        makeMetric({ metric_name: "Operating Cash Flow", value_usd: 34000000000, prior_year_value_usd: 27700000000, confidence: 0.96, period: "FY2025" }),
        makeMetric({ metric_name: "Cash and Cash Equivalents", value_usd: 34000000000, prior_year_value_usd: 33200000000, confidence: 0.95, period: "FY2025" }),
        makeMetric({ metric_name: "Research and Development", value_usd: 9800000000, prior_year_value_usd: 8000000000, confidence: 0.96, period: "FY2025" }),
      ],
      flaggedItems: [],
      xbrl: {
        Revenue: 68000000000,
        "Net Income": 26700000000,
        "Total Assets": 104000000000,
        "Total Liabilities": 33000000000,
        "Shareholders' Equity": 71000000000,
        "Operating Cash Flow": 34000000000,
        "Cash and Cash Equivalents": 34000000000,
        "Research and Development": 9800000000,
      },
    },
    {
      name: "Tesla, Inc.",
      ticker: "TSLA",
      cik: "0001318605",
      industry: "Consumer Discretionary",
      fiscal_year: 2025,
      period_end: "2025-12-31",
      fiscalYearEnd: "12-31",
      filing_url: "https://www.sec.gov/Archives/edgar/data/1318605/000131860525000012/tsla-20251231.htm",
      isPlaceholder: true,
      metrics: [
        makeMetric({
          metric_name: "Revenue",
          value_usd: 97000000000,
          prior_year_value_usd: 88000000000,
          confidence: 0.9,
          period: "FY2025",
          decision_state: "FLAGGED",
        }),
        makeMetric({ metric_name: "Net Income", value_usd: 18500000000, prior_year_value_usd: 14600000000, confidence: 0.88, period: "FY2025" }),
        makeMetric({ metric_name: "Total Assets", value_usd: 238000000000, prior_year_value_usd: 232000000000, confidence: 0.87, period: "FY2025" }),
        makeMetric({ metric_name: "Total Liabilities", value_usd: 90000000000, prior_year_value_usd: 86000000000, confidence: 0.86, period: "FY2025" }),
        makeMetric({ metric_name: "Shareholders' Equity", value_usd: 148000000000, prior_year_value_usd: 143000000000, confidence: 0.87, period: "FY2025" }),
        makeMetric({ metric_name: "Operating Cash Flow", value_usd: 24500000000, prior_year_value_usd: 21800000000, confidence: 0.87, period: "FY2025" }),
        makeMetric({ metric_name: "Cash and Cash Equivalents", value_usd: 46100000000, prior_year_value_usd: 43900000000, confidence: 0.88, period: "FY2025" }),
        makeMetric({ metric_name: "Research and Development", value_usd: 24500000000, prior_year_value_usd: 22900000000, confidence: 0.88, period: "FY2025" }),
      ],
      flaggedItems: [
        flag(
          makeMetric({
            metric_name: "Revenue",
            value_usd: 97000000000,
            prior_year_value_usd: 88000000000,
            confidence: 0.9,
            period: "FY2025",
            decision_state: "FLAGGED",
          }),
          "HIGH",
          ["FINANCIAL_ANOMALY"],
          ["FY2025 revenue increase of 10.2% vs. FY2024 requires manual review", "Delivery-based revenue recognition variance"],
          ["YOY_TOLERANCE", "REVENUE_REC"]
        ),
      ],
      xbrl: {
        Revenue: 97000000000,
        "Net Income": 18500000000,
        "Total Assets": 238000000000,
        "Total Liabilities": 90000000000,
        "Shareholders' Equity": 148000000000,
        "Operating Cash Flow": 24500000000,
        "Cash and Cash Equivalents": 46100000000,
        "Research and Development": 24500000000,
      },
    },
    {
      name: "Amazon.com, Inc.",
      ticker: "AMZN",
      cik: "0001018724",
      industry: "Consumer Discretionary",
      fiscal_year: 2025,
      period_end: "2025-12-31",
      fiscalYearEnd: "12-31",
      filing_url: "https://www.sec.gov/Archives/edgar/data/1018724/000101872425000029/amzn-20251231.htm",
      isPlaceholder: true,
      metrics: [
        makeMetric({ metric_name: "Revenue", value_usd: 637900000000, prior_year_value_usd: 574800000000, confidence: 0.95, period: "FY2025" }),
        makeMetric({ metric_name: "Net Income", value_usd: 59300000000, prior_year_value_usd: 39200000000, confidence: 0.94, period: "FY2025" }),
        makeMetric({ metric_name: "Total Assets", value_usd: 624800000000, prior_year_value_usd: 531000000000, confidence: 0.93, period: "FY2025" }),
        makeMetric({ metric_name: "Total Liabilities", value_usd: 316000000000, prior_year_value_usd: 285000000000, confidence: 0.92, period: "FY2025" }),
        makeMetric({ metric_name: "Shareholders' Equity", value_usd: 308800000000, prior_year_value_usd: 246000000000, confidence: 0.93, period: "FY2025" }),
        makeMetric({ metric_name: "Operating Cash Flow", value_usd: 115900000000, prior_year_value_usd: 84700000000, confidence: 0.93, period: "FY2025" }),
        makeMetric({ metric_name: "Cash and Cash Equivalents", value_usd: 78700000000, prior_year_value_usd: 73300000000, confidence: 0.92, period: "FY2025" }),
        makeMetric({ metric_name: "Research and Development", value_usd: 88500000000, prior_year_value_usd: 85700000000, confidence: 0.93, period: "FY2025" }),
      ],
      flaggedItems: [],
      xbrl: {
        Revenue: 637900000000,
        "Net Income": 59300000000,
        "Total Assets": 624800000000,
        "Total Liabilities": 316000000000,
        "Shareholders' Equity": 308800000000,
        "Operating Cash Flow": 115900000000,
        "Cash and Cash Equivalents": 78700000000,
        "Research and Development": 88500000000,
      },
    },
  ] as CompanyFixture[],
};

export function findCompanyByCik(cik: string): CompanyFixture {
  const match = FIXTURES.companies.find((c) => c.cik === cik);
  if (!match) throw new Error(`Unknown company ${cik}`);
  return match;
}

export function trueValueFor(company: CompanyFixture, metricName: string): number {
  return company.xbrl[metricName] ?? 0;
}

export function flaggedForCompany(company: CompanyFixture): FlaggedItem[] {
  return company.flaggedItems;
}

export function buildAnomalyExplanation(metric: FlaggedItem): AnomalyExplanation {
  const isExtraction = metric.escalation_types.includes("EXTRACTION_RISK");
  return {
    metric_name: metric.metric_name,
    title: isExtraction ? "Extraction risk" : "Financial anomaly",
    detail: metric.reasons.join("; "),
    severity: metric.severity,
  };
}
