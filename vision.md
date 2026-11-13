# TRACE — Vision

> **Automate the routine. Escalate the uncertain. Keep every decision accountable.**

This document describes what TRACE is trying to become, and the principles it is
built around. It is the north-star companion to the [README.md](README.md) (product
overview + developer tech stack) and the [workflow.md](workflow.md) (runtime flow).

## The one-paragraph vision

Every public company eventually files a 10-K. Somewhere inside that filing are the
eight numbers that move markets, move pensions, and move the machines — revenue,
net income, total assets, liabilities, equity, operating cash flow, cash, R&D — and
somewhere inside those numbers is the difference between a healthy quarter and a
material restatement.

Today, finding those numbers means reading a 300-page document full of tables,
footnotes, and management discussion, trusting an LLM's arithmetic, and hoping
nobody filed a restatement. TRACE's vision is to turn the 10-K from a document humans
reluctantly read into a **structured, verifiable, auditable financial evidence
trail**:

1. Machines do the routine reading — fetch, extract, validate, score confidence.
2. Humans own the judgment — only the uncertain or anomalous numbers ever stop the
   pipeline and wait for a person.
3. Every decision is recorded — with evidence, with hashes, with an unbroken chain.

The long-term vision is not "an LLM that reads filings." It is a **filing-auditor
that a company, its auditors, or its investors can trust** — where an auditor can ask
"why did you flag this?" and get back a source snippet, the deterministic signals, and
a defensible answer.

## Vision pillars

### 1. Accuracy is the product

The core metric of the product is not "did the LLM find the number" but
**did the extracted number survive comparison against the SEC's own XBRL facts**.
The accuracy report (`final_accuracy_pct`, `original_accuracy_pct`, per-metric
`original_match` / `final_match`) is a first-class feature, displayed on the
Accuracy page, exported to CSV, and used to compute escalation quality
(precision / recall / false-auto-approval rate).

Everything downstream — confidence, validation, escalation, auto-approval — exists
to make that comparison trustworthy.

### 2. Confidence comes from evidence, not vibes

The LLM's self-reported confidence is *one input*, never the gate. A deterministic
score is computed from reproducible signals:

- snippet grounding (the value's digits appear in the source row text),
- concept match (label maps to a canonical metric role),
- unit consistency, period consistency, column/period match,
- cross-statement check (Net Income vs. cash-flow opening line),
- prior-year sanity,
- extractor agreement (Gemini vs. Groq, compared after full-USD normalization),
- LLM confidence (weighted last, capped).

Hard caps and renormalization keep the score honest, and the threshold
(`confidence_threshold`, default 0.85) is user-configurable.

### 3. Automate the routine, escalate the uncertain

The pipeline never guesses its way to "approved":

- Clean, well-grounded metrics are auto-approved — but only after deterministic
  validation passes.
- Anything flagged — extraction risk or financial anomaly — pauses at a LangGraph
  human-in-the-loop interrupt and requires an explicit `APPROVED` / `CORRECTED` /
  `REJECTED` decision.
- Red-tier items (FAIL / HIGH / RED / CRITICAL severity) require an explicit
  `acknowledge_risk` acknowledgment before approval.
- No silent approvals: unresolved flags leave the thread paused, and Submit in the
  UI stays disabled until every flagged item has a decision.

### 4. Accountability is non-negotiable

- The audit log is **append-only** (SQLite triggers reject UPDATE/DELETE; a unique
  idempotency index prevents duplicate rows per `(thread_id, metric_name, decision)`).
- Every row carries a **SHA-256 hash chain** (`prev_hash` → `row_hash`) over the
  decision content; the chain can be verified at any time and is tamper-evident.
- Workflow checkpoints persist per `thread_id` (SQLite via `SqliteSaver` or MongoDB
  via `MongoDBSaver`), so an interrupted audit can be resumed exactly where it stopped.

### 5. Resilience by design

- SEC EDGAR responses are cached on disk (`backend/data/sec_cache`), enabling fully
  offline audits from cache.
- LLM providers fail over (Gemini primary, Groq fallback) with extractor agreement
  measured on the final pair.
- The workflow is interrupt-driven, so a crash mid-audit resumes cleanly.

## What the vision is *not*

- TRACE is **not** a financial advisor, auditor substitute, or investment opinion
  generator. It produces findings and evidence; humans decide.
- TRACE does **not** ingest arbitrary documents beyond SEC 10-K-style tabular
  filings in its current scope.
- TRACE does **not** store PII; it stores financial facts and audit decisions.

## Where the vision lives in the code

| Vision element | Implementation |
|---|---|
| Automate routine | `backend/app/graph.py` (`AUTO_APPROVED` path), auto-approval accuracy metrics |
| Escalate uncertain | `backend/app/escalation.py`, confidence threshold, YoY threshold, red-tier guard |
| Keep decisions accountable | `backend/app/database.py` (append-only + hash chain), `backend/app/trace_contract.py` (`/audit-logs`, `/audit-logs/verify`) |
| Accuracy first | `backend/app/verification.py` (XBRL comparison), accuracy report builder |
| Confidence from evidence | `backend/app/confidence.py`, `backend/app/validator.py` |

See [workflow.md](workflow.md) for the step-by-step execution of this vision at
runtime, and [GATES.md](GATES.md) for the acceptance gates that keep the vision from
drifting.
