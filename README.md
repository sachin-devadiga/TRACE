# TRACE — Financial Filing Auditor

> **Automate the routine. Escalate the uncertain. Keep every decision accountable.**

TRACE audits SEC 10-K filings end to end: it fetches the filing, extracts the eight
core financial metrics with two independent readers, scores its own confidence, runs
deterministic validation, escalates anything doubtful to a human reviewer, and finally
compares every figure against the SEC's own XBRL facts. Every decision is written to an
append-only, hash-chained audit log.

This repository contains the complete product:

```
final TRACE/
├── backend/                 # FastAPI + LangGraph audit workflow (Python)
│   ├── app/
│   │   ├── api.py           # Combined app: backend + TRACE UI contract router
│   │   ├── trace_contract.py# UI TraceApi HTTP contract adapter
│   │   ├── graph.py         # fetch → extract → validate → interrupt → finalize
│   │   ├── validator.py     # Deterministic checks (snippet, period, equations, YoY)
│   │   ├── confidence.py    # Fixed-signal confidence scoring + extractor agreement
│   │   ├── llm_factory.py   # Gemini primary + Groq backup, run concurrently
│   │   ├── database.py      # Append-only audit log with a verified hash chain
│   │   ├── demo_seed.py     # Deterministic demo threads
│   │   └── ...              # edgar_client, evidence, escalation, units
│   ├── scripts/seed_demo.py # Seeds the demo threads + audit rows
│   ├── tests/               # Backend test suite (contract, regression, gates)
│   ├── requirements.txt
│   └── .env.example
├── frontend/                # Vite + React + TypeScript dashboard
│   ├── src/
│   │   ├── api/             # TraceApi contract, HTTP client, in-browser mock
│   │   ├── pages/           # overview, new-audit, review-queue, filings,
│   │   │   # filing-detail, accuracy, audit-log, settings
│   │   └── components/      # badges, charts, guided demo, layout
│   ├── e2e/                 # Playwright suites (live backend + mock mode)
│   └── .env.example
├── scripts/                 # preflight, SEC cache warmer, SEC fixtures fetcher
├── render.yaml              # Render blueprint (backend + frontend services)
├── GATES.md                 # Demo gates & judge checklist
├── README.md                # ← you are here
├── vision.md                # ← product north star
└── workflow.md              # ← runtime audit flow
```

## Why TRACE exists ([vision.md](vision.md))

> **Automate the routine. Escalate the uncertain. Keep every decision accountable.**

Public companies file 10-Ks constantly; auditors and investors read them, or wish they
did. TRACE exists so that the *routine* reading — fetch, extract eight core metrics,
validate, score confidence — is done by a machine that can show its work, while any
*doubtful* number stops the pipeline and waits for a human. The long-term goal is a
verifiable financial evidence trail per filing, where every decision is defensible and
every figure can be checked against the SEC's own XBRL facts.

## Tech stack — what a developer needs to know

### Architecture at a glance

```
                 ┌────────────────────────────┐
                 │        SEC EDGAR            │
                 │  (submissions + company    │
                 │   directory + XBRL facts)  │
                 └─────────────┬──────────────┘
                               │ HTTP (cached on disk)
                 ┌─────────────▼──────────────┐
                 │   FastAPI backend           │
                 │   (app.api:app)             │
                 │   ├─ /threads/*  (UI)      │
                 │   ├─ /audit-logs*           │
                 │   ├─ /demo/*                │
                 │   └─ legacy /audit/* (off) │
                 │   LangGraph state machine  │
                 │   SQLite / MongoDB store   │
                 └─────────────┬──────────────┘
                               │ TraceApi over HTTP
                 ┌─────────────▼──────────────┐
                 │   React + Vite frontend     │
                 │   (TraceApi interface,      │
                 │    mock impl in-browser)   │
                 └────────────────────────────┘
```

- The frontend **never** talks to the backend implementation details. It goes through
  the `TraceApi` interface (`frontend/src/api/contract.ts`, types in `types.ts`) with two
  implementations: `HttpTraceApi` (real backend) and `MockTraceApi` (in-browser demo
  server). Switching modes is a one-line env var (`VITE_API_MODE`).

### Backend (`backend/`)

| Concern | Technology / module |
|---|---|
| Web framework | **FastAPI** (`fastapi==0.162.5`) + **Uvicorn** (`uvicorn[standard]`) |
| Workflow engine | **LangGraph** (`langgraph==1.2.20`) with `StateGraph` of five nodes; checkpointing via `langgraph-checkpoint-sqlite` (`SqliteSaver`) or `langgraph-checkpoint-mongodb` (`MongoDBSaver`) |
| LLM layer | **LangChain** (`langchain-core==1.8.0`, `langchain-openai` for **Groq**, `langchain-google-genai` for **Gemini**); primary/fallback run concurrently, agreement compared after USD normalization |
| Schemas | **Pydantic v2** (`pydantic==2.13.4`) for API models and metrics/checks |
| Storage | **SQLite** (`backend/data/auditor.db` audit log + `backend/data/checkpoints.db` checkpoints, WAL, append-only triggers, SHA-256 hash chain) — or **MongoDB** when `MONGO_URI` is set |
| SEC client | `requests` + `beautifulsoup4`/`lxml`; on-disk cache in `backend/data/sec_cache`; User-Agent + rate-limit handling |
| XBRL | SEC company facts via EDGAR; USD normalization (`app/units.py`) |
| Confidence | `app/confidence.py` — weighted signals (snippet grounding, concept, unit, period, column, cross-statement, prior-year sanity, extractor agreement, LLM) with hard caps; threshold via `CONFIDENCE_HIGH_THRESHOLD` |
| Validation | `app/validator.py` — pure deterministic checks (snippet grounding, extractor agreement, column/period, cross-statement, accounting equation, YoY, segments) |
| Escalation | `app/escalation.py` — `EXTRACTION_RISK` vs `FINANCIAL_ANOMALY` typing |

### Frontend (`frontend/`)

| Concern | Technology |
|---|---|
| UI framework | **React 18** + **TypeScript** (`typescript==5.5.4`) |
| Build tool | **Vite** (`vite==5.4.11`); `npm run build` = `tsc --noEmit` + `vite build` |
| Routing | **React Router** v6 |
| Server state | **TanStack Query** v5 (poll running threads every 2 s until terminal) |
| Styling | **Tailwind CSS** + **Radix UI** primitives (shadcn-style in `src/components/ui/`) |
| Charts | **Recharts** |
| Icons / feedback | `lucide-react`, `sonner` |
| Tests (e2e) | **Playwright** (`@playwright/test`) — projects `live` (real backend) and `mock` (in-browser demo), plus `production-build` |

### Tooling & quality

- Backend tests: **pytest** (+ `mongomock` for the MongoDB paths; no server needed in CI).
- Formatting/lint: ESLint, Prettier (frontend); standard Python tooling via pytest/ruff-less setup.
- CI: **GitHub Actions** (`.github/workflows/ci.yml`) — backend pytest on Python 3.11;
  frontend `npm ci` → `npm run build` → boot backend → Playwright `--project=live`.

### Infrastructure / deployment

- **Render** blueprint ([render.yaml](render.yaml)): `trace-backend` (Python 3.11 web,
  `uvicorn app.api:app`, health check `GET /health`) and `trace-frontend` (static,
  build `npm ci && npm run build`, publish `./dist`, SPA rewrite `/* → /index.html`).
- Secrets via Render (never in git): `GEMINI_API_KEY`, `GROQ_API_KEY`, `VITE_API_BASE`.
- Optional durable state: attach a Render disk and set `AUDITOR_DB_PATH` /
  `AUDITOR_CHECKPOINT_DB` (defaults: `backend/data/auditor.db`, `backend/data/checkpoints.db`).

## Quick start

```bash
# Backend — http://127.0.0.1:8000
cd backend
pip install -r requirements.txt
cp .env.example .env          # add GEMINI_API_KEY and GROQ_API_KEY
python -m uvicorn app.api:app --reload

# Frontend — http://127.0.0.1:5173
cd frontend
npm install
cp .env.example .env          # VITE_API_MODE=http, VITE_API_BASE=http://127.0.0.1:8000
npm run dev
```

Open <http://127.0.0.1:5173>. The header shows **DATA MODE: Live** when the backend is up,
**Demo data** in mock mode.

## The nine workflow steps

See [workflow.md](workflow.md) for the exact route:

```
Filing retrieval → Extraction → Confidence scoring → Validation
   → Automated decision → Human review → Decision and resume
   → XBRL verification → Audit history
```

1. **Fetch** the latest 10-K from SEC EDGAR (cached, rate-limited, `SEC_USER_AGENT`).
2. **Extract** the eight metrics with Gemini and Groq **in parallel**; compare to produce
   an extractor-agreement signal.
3. **Score** confidence from fixed deterministic signals (snippet grounding, concept
   match, unit/period consistency, prior-year sanity, extractor agreement).
4. **Validate** with deterministic rules: snippet grounding, column/period match,
   accounting equation, cross-statement consistency, segment tables and YoY.
5. **Route**: clean filings are auto-approved, anything flagged pauses for a human.
6. **Verify** against the SEC's own XBRL facts and write the decision to the
   append-only audit log.

## API contract

The UI speaks to a single `TraceApi` interface over HTTP; the backend exposes it through
the additive router in `backend/app/trace_contract.py`:

| UI method | Backend route |
|---|---|
| `health()` | `GET /health` |
| `listCompanies()` | `GET /companies` |
| `startAudit()` | `POST /audits` |
| `listThreads()` | `GET /threads` |
| `getStatus()` | `GET /threads/{thread_id}/status` |
| `getTimeline()` | `GET /threads/{thread_id}/timeline` |
| `resume()` | `POST /threads/{thread_id}/resume` |
| `listAuditLogs()` | `GET /audit-logs` |
| `verifyChain()` | `GET /audit-logs/verify` |
| `resetDemo()` | `POST /demo/reset` |
| `tamperWithLastRow()` | `GET /demo/tamper` |
| `setFailureMode()` | `POST /demo/failure-mode` |

Conventions: backend reports percentages, UI expects fractions (adapter converts);
`fiscal_year` accepts `FY2026`, `2026` or `2026-09-26`.

## Tests

```bash
# Backend
cd backend && python -m pytest tests/ -q

# Frontend
cd frontend && npx playwright test          # live + mock projects
npm run build                               # tsc --noEmit + vite build
```

CI (`.github/workflows/ci.yml`) runs backend tests and the frontend build + live
Playwright suite on every push/PR to `main`.

## Demo data

```bash
cd backend && python scripts/seed_demo.py
```

Seeds `audit-seed-paused` (Apple FY2026, two flagged items) and `audit-seed-completed`
(Microsoft, auto-approved). Resettable from the UI (**Settings → Reset demo data**) or
`POST /demo/reset`. The audit log is never deleted.

## Configuration

| Variable | Where | Meaning |
|---|---|---|
| `GEMINI_API_KEY`, `GROQ_API_KEY` | `backend/.env` | LLM providers (required for live audits) |
| `GEMINI_THINKING_BUDGET` | `backend/.env` | Gemini reasoning budget; `0` keeps an audit ~10–15 s |
| `SEC_USER_AGENT`, `SEC_REQUEST_DELAY` | `backend/.env` | SEC fair-access policy |
| `CONFIDENCE_HIGH_THRESHOLD`, `CONFIDENCE_WEIGHTS` | `backend/.env` | Confidence policy |
| `AUDITOR_DB_PATH`, `AUDITOR_CHECKPOINT_DB` | `backend/.env` | Storage locations |
| `MONGO_URI`, `MONGO_DB_NAME`, `MONGO_SERVER_SELECTION_TIMEOUT_MS` | `backend/.env` | Optional MongoDB switch-over |
| `DEMO_RESET_ENABLED`, `DEMO_TAMPER_ENABLED`, `DEMO_FAILURE_MODE` | `backend/.env` | Demo controls |
| `VITE_API_MODE`, `VITE_API_BASE` | `frontend/.env` | `mock` (default) / `http` |

## Deploy

Render blueprint: backend on Python runtime, frontend as static site, SPA rewrite,
health check on `/health`. Full walkthrough: push to GitHub → Render Blueprint → supply
the three secrets (`GEMINI_API_KEY`, `GROQ_API_KEY`, `VITE_API_BASE`) → set backend
`CORS_ORIGINS` → save → verify `DATA MODE: Live`. See [render.yaml](render.yaml) for the
declarative version.

## Documentation map

| File | Purpose |
|---|---|
| [vision.md](vision.md) | Product vision, pillars, non-goals |
| [workflow.md](workflow.md) | Runtime audit flow, routes, resume, status machine |
| [GATES.md](GATES.md) | Demo gates, quality gates, judge questions |
| [frontend/docs/architecture.md](frontend/docs/architecture.md) | UI architecture deep-dive |
| [frontend/docs/demo_script.md](frontend/docs/demo_script.md) | Guided demo script |
| [frontend/docs/backup-recording-plan.md](frontend/docs/backup-recording-plan.md) | Backup video / cache recording plan |

## Notes for production

- The workflow is stateful (SQLite checkpoints + append-only log under `backend/data/`);
  point `AUDITOR_DB_PATH` / `AUDITOR_CHECKPOINT_DB` at durable storage.
- CORS defaults cover localhost; set `CORS_ORIGINS` for a deployed frontend origin.
- Secrets live in git-ignored `.env`; only `.env.example` is tracked.
