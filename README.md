# TRACE — Financial Filing Auditor

TRACE audits SEC 10-K filings end to end: it fetches the filing, extracts the eight
core financial metrics with two independent readers, scores its own confidence,
runs deterministic validation, escalates anything doubtful to a human reviewer, and
finally compares every figure against the SEC's own XBRL facts. Every decision is
written to an append-only, hash-chained audit log.

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
│   ├── tests/               # 13 backend tests (contract + regression)
│   ├── requirements.txt
│   └── .env.example
├── frontend/                # Vite + React + TypeScript dashboard
│   ├── src/
│   │   ├── api/             # TraceApi contract, HTTP client, in-browser mock
│   │   ├── pages/           # overview, new-audit, review-queue, filings,
│   │   │                    # filing-detail, accuracy, audit-log, settings
│   │   └── components/      # badges, charts, guided demo, layout
│   ├── e2e/                 # Playwright suites (live backend + mock mode)
│   └── .env.example
└── README.md
```

## Quick start

```powershell
# 1. Backend — http://127.0.0.1:8000
cd "C:\Users\sachi\Videos\final TRACE\backend"
pip install -r requirements.txt
copy .env.example .env          # then add GEMINI_API_KEY and GROQ_API_KEY
python -m uvicorn app.api:app --reload

# 2. Frontend — http://127.0.0.1:5173
cd "C:\Users\sachi\Videos\final TRACE\frontend"
npm install
copy .env.example .env          # VITE_API_MODE=http, VITE_API_BASE=http://127.0.0.1:8000
npm run dev
```

Open <http://127.0.0.1:5173>. The header shows **DATA MODE: Live**, which means the
dashboard is talking to the real backend rather than the in-browser demo server.

## What the reviewer sees

Every escalated metric carries a plain-language reason, the rules that fired, and a
badge that explains itself:

- **Extraction risk** — the number itself may be wrong (row not grounded in the
  filing, wrong period column, or the two readers disagreed).
- **Financial anomaly** — the number is plausible but moves unusually versus the
  prior year.

The review queue states exactly how many flagged items still need a decision, and
Submit stays disabled until every one of them has one — so nothing is ever silently
approved.

## Contract

The UI speaks to a single `TraceApi` interface over HTTP
(`frontend/src/api/contract.ts`). The backend exposes that contract through the
additive router in `backend/app/trace_contract.py`:

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

Two conventions matter:

- The backend reports percentages (`87.5`); the UI expects fractions (`0.875`). The
  adapter converts every `*_pct` field, so the dashboard shows `87.5%`.
- `fiscal_year` accepts `FY2026`, `2026` or `2026-09-26` and always renders `2026`.

## How an audit runs

```
Filing retrieval → Extraction → Confidence scoring → Validation
   → Automated decision → Human review → Decision and resume
   → XBRL verification → Audit history
```

1. **Fetch** the latest 10-K from SEC EDGAR (cached per process, `SEC_USER_AGENT`
   on every request, rate limited).
2. **Extract** the eight metrics with Gemini and Groq **in parallel**; the values
   are compared to produce an extractor-agreement signal.
3. **Score** confidence from fixed deterministic signals (snippet grounding, concept
   match, unit and period consistency, prior-year sanity, extractor agreement).
4. **Validate** with deterministic rules: snippet grounding, column/period match,
   accounting equation, cross-statement consistency, segment tables and YoY.
5. **Route**: clean filings are auto-approved, anything flagged pauses for a human.
6. **Verify** against the SEC's own XBRL facts and write the decision to the
   append-only audit log.

## Tests

```powershell
# Backend (13 tests)
cd "C:\Users\sachi\Videos\final TRACE\backend"
python -m pytest tests/ -q

# Frontend (9 live tests against the running backend + 1 mock test)
cd "C:\Users\sachi\Videos\final TRACE\frontend"
npx playwright test

# Type check and production build
npm run build
```

The Playwright suite has two projects: `live` (the real backend on port 8000) and
`mock` (the in-browser demo server on port 5174).

## Demo data

```powershell
cd "C:\Users\sachi\Videos\final TRACE\backend"
python scripts/seed_demo.py
```

Seeds `audit-seed-paused` (Apple FY2026, two flagged items waiting for review) and
`audit-seed-completed` (Microsoft, auto-approved). The Apple figures are the real
FY2026 10-K values, so resuming the paused thread produces a meaningful XBRL
comparison.

The same thing is available from the UI: **Settings → Reset demo data** (or
`POST /demo/reset`). It clears the workflow state and re-seeds the demo threads; the
append-only audit log is never deleted.

## Configuration

| Variable | Where | Meaning |
|---|---|---|
| `GEMINI_API_KEY`, `GROQ_API_KEY` | `backend/.env` | The two LLM providers (required for live audits) |
| `GEMINI_THINKING_BUDGET` | `backend/.env` | Gemini reasoning budget; `0` keeps an audit near 10-15s |
| `SEC_USER_AGENT` | `backend/.env` | Contact string sent to SEC EDGAR (required by SEC policy) |
| `DEMO_RESET_ENABLED` | `backend/.env` | Allow `POST /demo/reset` (default on) |
| `DEMO_TAMPER_ENABLED` | `backend/.env` | Allow `POST /demo/tamper` (default off; it breaks the hash chain on purpose) |
| `VITE_API_MODE` | `frontend/.env` | `http` for the real backend, `mock` for the in-browser demo |
| `VITE_API_BASE` | `frontend/.env` | Backend base URL |

## Deploying to Render

The repository contains a [Render blueprint](render.yaml) that creates both
services:

| Service | Type | What it serves |
|---|---|---|
| `trace-backend` | web (Python) | The FastAPI + LangGraph audit API |
| `trace-frontend` | web (static) | The built Vite dashboard |

### Step 1 — create the Blueprint

1. Push this repository to GitHub (see below).
2. In Render: **New → Blueprint** → select the repository → **Apply**.
3. Render asks for the three secrets it cannot read from the repo:
   - `GEMINI_API_KEY`
   - `GROQ_API_KEY`
   - `VITE_API_BASE` (leave it for now — step 3)

### Step 2 — note the backend URL

The backend is live within a couple of minutes at:

```
https://trace-backend.onrender.com
```

Check it: `https://trace-backend.onrender.com/health` must return
`{"status":"ok","version":"2.0.0"}`.

The free plan sleeps after 15 minutes of inactivity, so the first request can
take ~30 seconds while the service wakes up. The dashboard says so explicitly
instead of showing an empty page.

### Step 3 — point the frontend at the backend

1. Open the `trace-frontend` service → **Settings → Environment**.
2. Set `VITE_API_BASE` to `https://trace-backend.onrender.com`.
3. Set `CORS_ORIGINS` on `trace-backend` to include the frontend origin:
   `http://localhost:5173,http://127.0.0.1:5173,https://trace-frontend.onrender.com`
   (the blueprint already contains this value).
4. Save on both services. Render rebuilds the frontend with the new API base.

### Step 4 — verify

Open `https://trace-frontend.onrender.com`. The header must show **DATA MODE:
Live**. Deep links such as `/review` and `/filings/<thread_id>` work because the
blueprint rewrites every path to `index.html`.

### Durable data (optional)

By default the audit log and the workflow checkpoints live in the service's
ephemeral filesystem, so they are lost on the next deploy. To keep them:

1. Create a **Disk** (1 GB is plenty) and attach it to `trace-backend` at
   `/var/data`.
2. Set `AUDITOR_DB_PATH=/var/data/auditor.db` and
   `AUDITOR_CHECKPOINT_DB=/var/data/checkpoints.db` on the service.

### Testing a production build locally

```powershell
cd "C:\Users\sachi\Videos\final TRACE\frontend"
$env:VITE_API_MODE="http"; $env:VITE_API_BASE="http://127.0.0.1:8000"
npm run build
python scripts/serve-dist.py 4173 dist      # static server with SPA fallback
npx playwright test e2e/production-build.spec.ts
```

That test drives the built `dist/` folder — exactly what Render serves — against
the live backend, including deep links.

## MongoDB (optional, environment-driven)

MongoDB is **off by default** — the app uses the local SQLite files. Set one
environment variable and the storage switches over, with no code change:

| Variable | Meaning |
|---|---|
| `MONGO_URI` | Connection string, e.g. `mongodb+srv://user:pass@cluster.mongodb.net` |
| `MONGO_DB_NAME` | Database name (default `trace`) |
| `MONGO_SERVER_SELECTION_TIMEOUT_MS` | How long to wait for a server (default `8000`) |

When `MONGO_URI` is set:

- the **append-only audit log** moves to the `audit_logs` collection, keeping the
  same SHA-256 hash chain (`prev_hash` → `row_hash`) and the same verifier;
- the **workflow checkpoints** move to the `checkpoints` / `checkpoint_writes`
  collections via LangGraph's `MongoDBSaver`;
- the API contract, the UI and the audit-log format are **unchanged** — a row
  produced by MongoDB hashes identically to the same row produced by SQLite.

Check which backend is live at any time: `GET /health` returns
`{"status":"ok","version":"2.0.0","storage":"MongoDB (trace)"}`, and the
**Settings** page shows the same value.

### On Render

The blueprint already declares `MONGO_URI` as a secret (`sync: false`), so:

1. Open the `trace-backend` service → **Settings → Environment**.
2. Paste your Atlas connection string into `MONGO_URI`.
3. Save — the service restarts and picks up MongoDB.

Nothing else changes: no rebuild, no config edit.

### Atlas checklist

- Allow Render's outbound IPs, or set the cluster's IP access list to `0.0.0.0/0`
  for a demo (tighter for production).
- Use a **Database User** with read/write on the `trace` database, not the
  Atlas admin user.
- Atlas needs TLS — the connection string already includes `mongodb+srv://`.

### Notes

- The audit-log writer serialises inserts with a process-level lock and keeps
  the chain head in a single document. Run **one** backend instance (the Render
  free tier is one instance). Multiple instances would need a distributed lock.
- The MongoDB tests run against an in-process mock, so CI needs no server:

```powershell
cd "C:\Users\sachi\Videos\final TRACE\backend"
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/ -q        # 30 tests, no MongoDB required
```

## Push to GitHub

```powershell
cd "C:\Users\sachi\Videos\final TRACE"
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

The repository already ignores `.env`, `node_modules`, `data/` and `*.db`, so the
API keys stay out of git. Only `.env.example` files are tracked.

## Notes for production

- The workflow is stateful and uses SQLite checkpoints plus an append-only audit log
  under `backend/data/`. A hosted deployment should point `AUDITOR_DB_PATH` and
  `AUDITOR_CHECKPOINT_DB` at durable storage.
- CORS is currently restricted to `http://localhost:5173` and
  `http://127.0.0.1:5173`; add the deployed frontend origin before going live.
- Secrets live in `.env` files that are git-ignored. Only `.env.example` is tracked.
