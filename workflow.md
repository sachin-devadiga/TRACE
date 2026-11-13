# TRACE — Audit Workflow

This document is the **runtime map** of what happens after a user hits "run audit".
It describes the exact graph route through the backend, what each node does, what it
writes, and how the human-review resume works.

- Product context: [README.md](README.md)
- North star: [vision.md](vision.md)
- Acceptance gates: [GATES.md](GATES.md)

## TL;DR route

```
POST /audits ──► START ──► fetch_node ──► extract_node ──► validate_node
                                                    │            │
                                                    │ no flags    │ flags found
                                                    │            ▼
                                                    │       human_review_node
                                                    │            │  (LangGraph interrupt)
                                                    │            │  human decides
                                                    │            │  (POST /threads/:id/resume)
                                                    │            ▼
                                                    │      finalize_node ──► END (COMPLETED)
                                                    │
                                                    └──────────────────────┘ (no flags → skip review, finalize)
```

The compiled LangGraph has exactly six edges:

```
START ──► fetch_node ──► extract_node ──► validate_node ──► human_review_node ──► finalize_node ──► END
```

## Full route diagram

```mermaid
flowchart TD
    S([START]) --> F[fetch_node<br/>status → FETCHED]
    F --> E[extract_node<br/>status → EXTRACTED]
    E --> V[validate_node<br/>status → VALIDATED]

    V -->|flagged_items empty| HR[human_review_node]
    V -->|flagged_items non-empty| HR

    HR -->|no flags| FIN[finalize_node]
    HR -->|flagged + interrupt hit| RES[POST /threads/{id}/resume<br/>human decisions]
    RES --> HR
    HR -->|flagged + decisions applied| FIN

    FIN --> EN([END<br/>status → COMPLETED])

    classDef startend fill:#e0e7ff,stroke:#4338ca;
    classDef done fill:#dcfce7,stroke:#15803d;
    class S startend; class EN done;
```

Equivalent ASCII form:

```
        ┌────────────── START
        │
        ▼
   ┌─────────┐   ┌───────────┐   ┌─────────────┐
   │ FETCH   │──►│ EXTRACT   │──►│ VALIDATE    │
   │ FETCHED │   │ EXTRACTED │   │ VALIDATED   │
   └─────────┘   └───────────┘   └──────┬──────┘
                                       │ flags?
                     ┌─────────────────┴─────────────────┐
                     │ no flags                           │ flags found
                     ▼                                    ▼
              ┌──────────────┐              ┌──────────────────┐
              │ human_review │              │ human_review     │
              │ (AUTO_APPROVE│              │ (PAUSED_FOR_REVIEW│
              │  if clean)   │              │  + interrupt)    │
              └──────┬───────┘              └────────┬─────────┘
                     │ no flags                           │ resume w/ decisions
                     ▼                                    │ (Command resume)
              ┌──────────────┐              ┌────────┴─────────┐
              │  finalize    │◄──────────────┤ human_review     │
              │ (COMPLETED) │   decisions   │ (re-run node)    │
              └──────┬───────┘              └──────────────────┘
                     │
                     ▼
              ┌──────────┐
              │   END    │
              │ COMPLETED│
              └──────────┘
```

## Step-by-step route

### 1. `fetch_node` — `START → FETCHED`

- Input: `cik` (normalized, e.g. `0000320193`) and optional `accession_number`.
- Calls `fetch_latest_10k_tables(cik, accession)` from `app/edgar_client.py`.
- Uses the **on-disk SEC cache** (`backend/data/sec_cache`, keyed by URL hash);
  online requests refresh the cache, offline/`cache_only` mode reads from it.
- SEC policy: `SEC_USER_AGENT` on every request, `SEC_REQUEST_DELAY` spacing.
- Guard: if `tables_markdown` already present, node is a no-op (checkpoint protects reruns).
- Output: `tables_markdown` (list of `{title, category, markdown, row_count, char_count}`),
  `filing_metadata`, `status="FETCHED"`.

### 2. `extract_node` — `EXTRACTED`

- Input: `tables_markdown` from step 1.
- Builds a structured extraction prompt (8 required metric names, exact-name rules,
  current-vs-prior year columns, negatives in parentheses, consolidated totals,
  source snippet = exact row text).
- Runs **two extractors concurrently** (Gemini primary, Groq fallback via `llm_factory.py`);
  agreement is compared by canonical role after full-USD normalization (tolerance 0.5%).
- Enforces coverage: **at least 8 metrics / all required roles must be found** or the
  run fails with a clear error (no silent omission).
- Attaches evidence context: source table, header, row, value column, period column,
  cross-statement note.
- Writes `extracted_metrics` + `original_metrics` (immutable pre-review copy) +
  `status="EXTRACTED"`.

### 3. `validate_node` — `VALIDATED` or `PAUSED_FOR_REVIEW`

Pure deterministic rules (no LLM calls):

1. **Snippet Grounding** — value digits must appear in `source_snippet`; ungrounded →
   LLM confidence −0.25 and flag WARNING.
2. **Extractor Agreement** — `agree` / `disagree` / `fallback-unavailable`.
3. **Column / Period** — extracted value must sit in the current-period column.
4. **Cross-Statement Check** — Net Income vs. cash-flow opening line.
5. **Accounting Equation**, **YoY Analysis** (vs. user threshold), **Segment
   Validation / Segment Table Check**.

Returns `flagged_items` (only FAILED/WARNING with severity ranking) and
`routing_decision`.

### 4. Human review — the interrupt branch

```
human_review_node (flagged?) ──► no flags ──► AUTO_APPROVE ──► finalize
                          │
                          ▼ flags present
                     interrupt(payload)
                     {thread_id, company_name, flagged_items, anomaly_explanations}
```

- The node **must not write anything before `interrupt()`**; on resume LangGraph re-runs
  the node from the top, and all writes below are idempotent.
- Resume: `POST /threads/{thread_id}/resume` → `graph.invoke(Command(resume=payload))`.
- Decisions mapping: metric name → `{decision: APPROVED | CORRECTED | REJECTED,
  corrected_value?, notes?, acknowledge_risk?}`. `CORRECTED` requires a numeric
  `corrected_value`.
- Red-tier guard: severity `FAIL | HIGH | RED | CRITICAL` requires `acknowledge_risk=true`
  before `APPROVED`.
- Audit writes use `INSERT OR IGNORE` + unique index → replay-safe.
- After decisions: status `HUMAN_REVIEWED`.

**Routing safety rule:** the LLM router (`agent_router`) is *advisory only*; even if it
says `AUTO_APPROVE`, any flagged item forces `ESCALATE`.

### 5. `finalize_node` — `COMPLETED`

- Idempotently logs `AUTO_APPROVED` rows for any extracted metric not yet decided/logged.
- Fetches SEC **XBRL ground truth** for the CIK/accession and builds the accuracy report:
  original vs. final vs. XBRL values, tolerance 1%, per-metric matches, escalation
  precision/recall, auto-approval accuracy, false-auto-approval count, escalation rate.
- Writes `accuracy_report` + `side_effect_counts`:
  `fetch_edgar`, `extract_metrics`, `validate`, `persist_audit_decisions`,
  `fetch_xbrl_truth`, `write_accuracy_report`, `finalize`.

## Status machine

```
STARTED ─► FETCHED ─► EXTRACTED ─► VALIDATED ─► PAUSED_FOR_REVIEW ─► HUMAN_REVIEWED
                                                              │                    │
                                                              ▼                    │
                                                        FINALIZE ──► COMPLETED ◄───────┘
```

- `PAUSED_FOR_REVIEW`: paused at the interrupt; `next` in the snapshot lists the
  waiting task.
- `HUMAN_REVIEWED` / `AUTO_APPROVED`: human/decision branch completed.
- `COMPLETED`: finalize done (accuracy report present, or XBRL lookup failed → error
  recorded inside the report while the audit trail is still written).
- `FAILED`: set by the API layer when the graph raises (`_mark_thread_failed`).

## API surface involved in the route

| Step | Route (UI TraceApi) | Backend handler |
|---|---|---|
| Start | `POST /audits` | `trace_start_audit` → `backend_start_audit` |
| Status | `GET /threads/{thread_id}/status` | `trace_thread_status` → `_state_response` |
| Timeline | `GET /threads/{thread_id}/timeline` | `trace_thread_timeline` |
| Resume | `POST /threads/{thread_id}/resume` | `trace_resume` → `backend_resume_audit` |
| Audit log | `GET /audit-logs` | `trace_audit_logs` |
| Verify chain | `GET /audit-logs/verify` | `trace_verify_chain` |

The UI talks exclusively through the `TraceApi` interface
(`frontend/src/api/contract.ts`); the backend binds it via the additive router in
`backend/app/trace_contract.py` (the legacy `/audit/*` routes are filtered out of the
combined app in `backend/app/api.py`).

## Hash chain and audit trail (side of the workflow)

- Insert: compute `row_hash = SHA256(prev_hash | payload…)`, store `prev_hash`/`row_hash`.
- Idempotency: unique index `(thread_id, metric_name, decision)`.
- Verify: `GET /audit-logs/verify` recomputes the chain; tamper demo breaks it on purpose.
- Timeline: `get_state_history` → ordered checkpoint events with step/status detail.

## Error handling in the route

- Graph exception → thread marked `FAILED`, API returns `502` with a clean error
  (no stack leak; details in server logs).
- Resume with no pending interrupt → `409`; already completed → `409`.
- Resume without valid decisions → `400`.
- XBRL ground-truth failure → finalize still records the audit and writes an accuracy
  report containing the error string (fail-soft, auditable).

## Demo / fault-injection hooks on the same route

- `POST /demo/failure-mode` — forces new audits into `FAILED`.
- `POST /demo/reset` — clears demo threads (gated by `DEMO_RESET_ENABLED`).
- `GET /demo/tamper` — appends a synthetic row to demonstrate chain verification
  (gated by `DEMO_TAMPER_ENABLED`).
