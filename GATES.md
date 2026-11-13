# TRACE demo gates — live verification checklist

All Gate 1 checks run against the **real backend** (`VITE_API_MODE=http`). Mock mode is not accepted evidence.

## Gate 1 — every mandatory requirement works live

| # | Requirement | Proof | Status |
|---|-------------|-------|--------|
| 3.1 | EDGAR fetch + offline cache | Fetch 5 companies, disable network, re-run from `backend/data/sec_cache` | `scripts/preflight.py` checks cache; run `pytest backend/tests/test_edgar_cache.py` |
| 3.2 | Extraction (8+ metrics, period, unit, snippet) | All five extract 8 metrics with `period`, `unit`, `source_snippet` | `pytest backend/tests/test_extraction_contract.py` |
| 3.3 | Confidence + threshold slider | Every metric has `confidence_score` + signal breakdown; moving the slider changes escalations | `pytest backend/tests/test_confidence_threshold.py` |
| 3.4 | Validation pass/fail | Segment check appears only when a segment table exists | `pytest backend/tests/test_validator.py` |
| 3.5 | Human review | Flagged value pauses; Approve/Edit/Reject work | `frontend/e2e/http-backend.spec.ts` + manual checklist below |
| 3.6 | Resume after restart | `side_effect_counts` stay 1 after kill + resume | `pytest backend/tests/test_resume_idempotency.py` |
| 3.7 | Dashboard | Pending/completed, evidence, validation, confidence visible | Playwright `ui-audit.spec.ts` |
| 3.8 | XBRL accuracy + audit log | Accuracy from XBRL; `GET /audit-logs/verify` passes | `pytest backend/tests/test_accuracy_xbrl.py` |

## Gate 2 — numbers are good

| Metric | Target | How measured |
|--------|--------|--------------|
| Extraction accuracy (8 core) | ≥ 95% | Accuracy report vs XBRL |
| False auto-approvals | 0 | Auto-approved rows where `original_match=false` |
| Calibration | high-confidence bucket more accurate | Calibration table on Accuracy page |
| Escalation rate | 10–30% | `escalated_count / metrics` |
| Escalation precision | maximise, report honestly | `escalation_precision_pct` |
| Fault injection | 100% caught, several metrics | `pytest backend/tests/test_fault_injection.py` |

## Gate 3 — automated reliability

- `pytest backend/tests/test_resume_idempotency.py` — kill at each step, double-submit, re-run, tamper, triggers, WAL, parallel companies, red-tier guard.

## Gate 4 — verification pitfalls

- Period-end matching (Apple Sept, NVIDIA Jan), duplicate XBRL facts across filings, tag fallbacks, missing ground truth = “no ground truth” not mismatch, units/signs, original 10-K not 10-K/A.
- `pytest backend/tests/test_verification_pitfalls.py`.

## Gate 5 — demo-proofing

1. `python scripts/preflight.py` — SEC User-Agent, cache for 5 companies, Ollama/API reachable, ports free, DBs clean.
2. `python scripts/preflight.py --reset` — one-command demo reset.
3. UI loading/empty/error states — Playwright `ui-states.spec.ts`.
4. Page refresh keeps state; backup video and cached screenshots.
5. Rehearse three times; fallback: offline cache + local SQLite + recorded backup.

## Judge questions

- **Confidence** is computed from deterministic, reproducible evidence signals (grounding, concept, unit, period, prior-year sanity, extractor agreement); the LLM's own certainty is only one weighted signal and never the gate.
- **Extractor never saw XBRL**: the extraction prompt only receives 10-K table text; XBRL is fetched separately for the accuracy report and is never in the prompt/context.
- **SEC down**: all fetched filings are cached locally; audits run from cache with the network off.
- **What went wrong**: first version displayed accuracy as 10000% (fraction/percent mix), and mock fixtures used hand-typed numbers; both were fixed by standardising `formatPct` on 0–100 and generating fixtures from a SEC script.
- **False auto-approvals**: report the live count; target is zero.
