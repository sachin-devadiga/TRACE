"""FastAPI backend for the PS-01 financial filing auditor.

Run from the project root with::

    uvicorn app.main:app --reload

The API is intentionally synchronous at its core: ``SqliteSaver`` wraps a
single SQLite connection, so a process-wide lock serialises graph access.
FastAPI executes these ``def`` endpoints in its worker threadpool.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import uuid
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator, model_validator

from app.store import (
    AUDIT_DECISIONS,
    get_audit_logs,
    initialize_database,
    list_thread_ids,
    thread_exists,
    verify_audit_chain,
)
from app.edgar_client import TARGET_COMPANIES, normalize_cik
from app.graph import (
    build_audit_graph,
    build_checkpointer,
    get_checkpoint_path,
)

from langgraph.graph import END
from langgraph.types import Command

__all__ = ["app"]

logger = logging.getLogger(__name__)

_GRAPH_LOCK = threading.RLock()
_GRAPH: Any = None
_CHECKPOINTER_CONNECTION: sqlite3.Connection | None = None


class StartAuditRequest(BaseModel):
    cik: str = Field(min_length=1, description="SEC CIK, with or without leading zeros.")
    confidence_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    yoy_threshold_pct: float = Field(default=30.0, ge=0.0)
    accession_number: str | None = Field(
        default=None,
        description="Audit this exact EDGAR filing instead of the newest 10-K.",
    )
    demo_only: bool = Field(
        default=False,
        description=(
            "Mark a forced-escalation demo run so it is excluded from headline "
            "accuracy and escalation-precision metrics."
        ),
    )

    @field_validator("cik")
    @classmethod
    def _normalize_cik(cls, value: str) -> str:
        return normalize_cik(value)


class HumanDecision(BaseModel):
    decision: Literal["APPROVED", "CORRECTED", "REJECTED"]
    corrected_value: float | None = None
    notes: str = ""
    acknowledge_risk: bool = False

    @model_validator(mode="after")
    def _require_correction(self) -> "HumanDecision":
        if self.decision == "CORRECTED" and self.corrected_value is None:
            raise ValueError("CORRECTED decisions require corrected_value.")
        return self


class ResumeAuditRequest(BaseModel):
    decisions: dict[str, HumanDecision] = Field(default_factory=dict)


def _get_graph():
    """Build the graph once and keep its checkpoint connection open."""
    global _GRAPH, _CHECKPOINTER_CONNECTION
    if _GRAPH is None:
        initialize_database()
        _CHECKPOINTER_CONNECTION, saver = build_checkpointer()
        _GRAPH = build_audit_graph(checkpointer=saver)
    return _GRAPH


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    global _GRAPH, _CHECKPOINTER_CONNECTION
    with _GRAPH_LOCK:
        if _CHECKPOINTER_CONNECTION is not None:
            _CHECKPOINTER_CONNECTION.close()
            _CHECKPOINTER_CONNECTION = None
        _GRAPH = None


def _cors_origins() -> list[str]:
    """Origins allowed to call the API.

    Defaults cover the local Vite dev server. A hosted deployment sets
    ``CORS_ORIGINS`` to the deployed frontend origin, for example
    ``https://trace-frontend.onrender.com``.
    """
    raw = (
        os.getenv("CORS_ORIGINS")
        or "http://localhost:5173,http://127.0.0.1:5173"
    )
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    return origins or ["http://localhost:5173", "http://127.0.0.1:5173"]


app = FastAPI(
    title="PS-01 Financial Filing Auditor",
    version="2.0.0",
    description=(
        "Stateful SEC 10-K audit workflow with SQLite checkpoints, "
        "human-in-the-loop review, and an append-only audit log."
    ),
    lifespan=lifespan,
)

# TRACE web client (Vite dev server). The Streamlit dashboard is unaffected.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    # Any local port is allowed by default so the dev server (5173), the mock
    # server (5174) and a locally served production build (4173) all work.
    # Deployed origins still have to be listed in CORS_ORIGINS.
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _mark_thread_failed(graph: Any, thread_id: str) -> None:
    """Persist a terminal failure and clear any runnable graph task."""
    try:
        graph.update_state(_config(thread_id), {"status": "FAILED"})
        graph.update_state(_config(thread_id), None, as_node=END)
    except Exception as error:  # noqa: BLE001 - cleanup must not mask the 502
        logger.warning(
            "Could not mark audit thread %s as FAILED: %s",
            thread_id,
            error,
        )


def _checkpoint_thread_ids() -> list[str]:
    """Return every audit thread that has stored checkpoint state."""
    return list_thread_ids()


def _thread_exists(thread_id: str) -> bool:
    return thread_exists(thread_id)


def _interrupt_payload(snapshot: Any) -> dict[str, Any] | None:
    for task in getattr(snapshot, "tasks", ()) or ():
        for item in getattr(task, "interrupts", ()) or ():
            value = getattr(item, "value", None)
            if isinstance(value, dict):
                return value
    return None


def _table_summary(tables: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for table in tables or []:
        if not isinstance(table, dict):
            continue
        markdown = table.get("markdown") or ""
        summaries.append(
            {
                "title": table.get("title"),
                "category": table.get("category"),
                "row_count": table.get("row_count")
                or (markdown.count("\n") + 1 if markdown else 0),
                "char_count": table.get("char_count") or len(markdown),
            }
        )
    return summaries


def _iso_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _state_response(thread_id: str, snapshot: Any) -> dict[str, Any]:
    values = dict(getattr(snapshot, "values", None) or {})
    checkpoint_at = _iso_timestamp(getattr(snapshot, "created_at", None))
    return {
        "thread_id": thread_id,
        "status": values.get("status", "UNKNOWN"),
        "created_at": checkpoint_at,
        "updated_at": checkpoint_at,
        "next": list(getattr(snapshot, "next", ()) or ()),
        "interrupt": _interrupt_payload(snapshot),
        "company_name": values.get("company_name", ""),
        "cik": values.get("cik"),
        "confidence_threshold": values.get("confidence_threshold"),
        "yoy_threshold_pct": values.get("yoy_threshold_pct"),
        "demo_only": bool(values.get("demo_only")),
        "routing_decision": values.get("routing_decision"),
        "filing_metadata": values.get("filing_metadata") or {},
        "tables": _table_summary(values.get("tables_markdown")),
        "tables_markdown": values.get("tables_markdown") or [],
        "extracted_metrics": values.get("extracted_metrics") or [],
        "original_metrics": values.get("original_metrics") or [],
        "validation_checks": values.get("validation_checks") or [],
        "flagged_items": values.get("flagged_items") or [],
        "anomaly_explanations": values.get("anomaly_explanations") or {},
        "human_decisions": values.get("human_decisions") or [],
        "accuracy_report": values.get("accuracy_report") or {},
        "side_effect_counts": values.get("side_effect_counts") or {},
        "escalation_precision_by_type": (
            values.get("accuracy_report") or {}
        ).get("escalation_precision_by_type")
        or {},
    }


def _existing_snapshot(graph: Any, thread_id: str) -> Any:
    if not _thread_exists(thread_id):
        raise HTTPException(status_code=404, detail=f"Unknown audit thread {thread_id!r}.")
    return graph.get_state(_config(thread_id))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/companies")
def companies() -> dict[str, Any]:
    """Read-only company catalogue mirrored from the EDGAR client."""
    return {
        "companies": [
            {"key": name, **profile}
            for name, profile in TARGET_COMPANIES.items()
        ],
        "count": len(TARGET_COMPANIES),
    }


@app.post("/audit/start")
def start_audit(request: StartAuditRequest) -> dict[str, Any]:
    """Start a new audit and run until completion or human-review interrupt."""
    thread_id = f"audit-{uuid.uuid4().hex}"
    initial_state = {
        "thread_id": thread_id,
        "cik": request.cik,
        "accession_number": request.accession_number or "",
        "company_name": "",
        "confidence_threshold": request.confidence_threshold,
        "yoy_threshold_pct": request.yoy_threshold_pct,
        "tables_markdown": [],
        "filing_metadata": {},
        "extracted_metrics": [],
        "original_metrics": [],
        "validation_checks": [],
        "flagged_items": [],
        "anomaly_explanations": {},
        "human_decisions": [],
        "accuracy_report": {},
        "routing_decision": "",
        "demo_only": request.demo_only,
        "side_effect_counts": {},
        "status": "STARTED",
    }

    with _GRAPH_LOCK:
        graph = _get_graph()
        try:
            graph.invoke(initial_state, _config(thread_id))
        except Exception as error:  # noqa: BLE001 - surface a clean API error
            _mark_thread_failed(graph, thread_id)
            raise HTTPException(
                status_code=502,
                detail=f"Audit workflow failed: {type(error).__name__}: {error}",
            ) from error
        snapshot = graph.get_state(_config(thread_id))

    return _state_response(thread_id, snapshot)


@app.get("/audit/threads")
def list_audit_threads() -> dict[str, Any]:
    """List every audit thread persisted in the checkpoint database."""
    with _GRAPH_LOCK:
        graph = _get_graph()
        threads: list[dict[str, Any]] = []
        for thread_id in _checkpoint_thread_ids():
            snapshot = graph.get_state(_config(thread_id))
            values = dict(getattr(snapshot, "values", None) or {})
            report = values.get("accuracy_report") or {}
            checkpoint_at = _iso_timestamp(getattr(snapshot, "created_at", None))
            threads.append(
                {
                    "thread_id": thread_id,
                    "status": values.get("status", "UNKNOWN"),
                    "created_at": checkpoint_at,
                    "updated_at": checkpoint_at,
                    "company_name": values.get("company_name", ""),
                    "cik": values.get("cik"),
                    "next": list(getattr(snapshot, "next", ()) or ()),
                    "flagged_count": len(values.get("flagged_items") or []),
                    "demo_only": bool(values.get("demo_only")),
                    "accuracy_pct": report.get("accuracy_pct"),
                    "original_accuracy_pct": report.get("original_accuracy_pct"),
                    "escalation_precision_pct": report.get("escalation_precision_pct"),
                    "escalation_precision_by_type": report.get(
                        "escalation_precision_by_type"
                    )
                    or {},
                    "escalation_recall_pct": report.get("escalation_recall_pct"),
                    "auto_approval_accuracy_pct": report.get(
                        "auto_approval_accuracy_pct"
                    ),
                    "auto_approved_count": report.get("auto_approved_count"),
                    "auto_approved_matches": report.get("auto_approved_matches"),
                    "auto_approved_compared": report.get("auto_approved_compared"),
                    "escalated_count": report.get("escalated_count"),
                    "genuine_escalations": report.get("genuine_escalations"),
                    "human_reviewed_count": report.get("human_reviewed_count"),
                    "unresolved_flagged_count": report.get("unresolved_flagged_count"),
                    "total_values": report.get("total_values"),
                    "matches": report.get("matches"),
                    "metrics_compared": report.get("metrics_compared"),
                    "original_matches": report.get("original_matches"),
                    "original_metrics_compared": report.get(
                        "original_metrics_compared"
                    ),
                }
            )
    return {"threads": threads, "count": len(threads)}


@app.get("/audit/{thread_id}/status")
def audit_status(thread_id: str) -> dict[str, Any]:
    """Return the current checkpointed state for one audit thread."""
    with _GRAPH_LOCK:
        graph = _get_graph()
        snapshot = _existing_snapshot(graph, thread_id)
    return _state_response(thread_id, snapshot)


@app.get("/audit/{thread_id}/timeline")
def audit_timeline(thread_id: str) -> dict[str, Any]:
    """Return the read-only checkpoint history (real timestamps, oldest first)."""
    with _GRAPH_LOCK:
        graph = _get_graph()
        _existing_snapshot(graph, thread_id)
        history = list(graph.get_state_history(_config(thread_id)))

    events = [
        {
            "checkpoint_id": (getattr(snapshot, "config", None) or {})
            .get("configurable", {})
            .get("checkpoint_id"),
            "created_at": _iso_timestamp(getattr(snapshot, "created_at", None)),
            "step": (getattr(snapshot, "metadata", None) or {}).get("step"),
            "source": (getattr(snapshot, "metadata", None) or {}).get("source"),
            "status": (getattr(snapshot, "values", None) or {}).get("status"),
            "next": list(getattr(snapshot, "next", ()) or ()),
        }
        for snapshot in history
    ]
    events.reverse()
    return {
        "thread_id": thread_id,
        "events": events,
        "count": len(events),
    }


@app.post("/audit/{thread_id}/resume")
def resume_audit(thread_id: str, request: ResumeAuditRequest) -> dict[str, Any]:
    """Resume a paused audit with human decisions and return the final report."""
    with _GRAPH_LOCK:
        graph = _get_graph()
        snapshot = _existing_snapshot(graph, thread_id)
        values = dict(getattr(snapshot, "values", None) or {})

        if values.get("status") == "COMPLETED":
            raise HTTPException(status_code=409, detail="Audit thread is already completed.")
        if not getattr(snapshot, "next", ()):
            raise HTTPException(
                status_code=409,
                detail="Audit thread is not paused at a human-review interrupt.",
            )

        resume_payload = {
            "decisions": {
                metric_name: decision.model_dump()
                for metric_name, decision in request.decisions.items()
            }
        }
        try:
            graph.invoke(Command(resume=resume_payload), _config(thread_id))
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except Exception as error:  # noqa: BLE001
            raise HTTPException(
                status_code=502,
                detail=f"Audit resume failed: {type(error).__name__}: {error}",
            ) from error

        snapshot = graph.get_state(_config(thread_id))

    return _state_response(thread_id, snapshot)


@app.get("/audit/logs")
def audit_logs(
    thread_id: str | None = None,
    decision: str | None = None,
) -> dict[str, Any]:
    """Return the append-only audit trail, optionally filtered."""
    if decision is not None and decision.strip().upper() not in AUDIT_DECISIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid decision {decision!r}; expected one of "
                f"{', '.join(AUDIT_DECISIONS)}."
            ),
        )
    logs = get_audit_logs(thread_id, decision=decision)
    return {"logs": logs, "count": len(logs)}


@app.get("/audit/verify")
def audit_verify() -> dict[str, Any]:
    """Verify the append-only audit row hash chain."""
    return verify_audit_chain()
