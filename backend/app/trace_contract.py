"""TRACE web-UI contract adapter for the PS-01 FastAPI backend.

The production TRACE UI (``trace-ui``) talks to a single :class:`TraceApi`
interface over HTTP.  This module binds that contract to the backend workflow
implemented in :mod:`app.main`, :mod:`app.graph` and :mod:`app.database` without
touching the workflow, validation, or audit semantics.

Contract implemented here (UI ``TraceApi`` parity):

- ``GET  /health``
- ``GET  /companies``
- ``POST /audits``
- ``GET  /threads``
- ``GET  /threads/{thread_id}/status``
- ``GET  /threads/{thread_id}/timeline``
- ``POST /threads/{thread_id}/resume``
- ``GET  /audit-logs``
- ``GET  /audit-logs/verify``
- ``POST /demo/reset``
- ``POST /demo/tamper``
- ``POST /demo/failure-mode``

Two conventions matter for the UI:

* The backend reports percentages (``87.5``); the UI expects fractions
  (``0.875``).  Every ``*_pct`` field is converted here.
* Every review item carries at least one human-readable reason sentence, so
  the reviewer never faces an empty "Why this was escalated" panel.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.store import backend_label, clear_threads, get_audit_logs, insert_audit_log, verify_audit_chain
from app.demo_seed import clear_threads, seed_demo_threads
from app.edgar_client import TARGET_COMPANIES
from app.escalation import ESCALATION_TYPE_LABELS, types_for_item
from app.main import (
    HumanDecision as BackendHumanDecision,
    ResumeAuditRequest as BackendResumeAuditRequest,
    StartAuditRequest as BackendStartAuditRequest,
    _checkpoint_thread_ids,
    _existing_snapshot,
    _get_graph,
    _GRAPH_LOCK,
    _state_response,
    resume_audit as backend_resume_audit,
    start_audit as backend_start_audit,
)

__all__ = ["router"]

logger = logging.getLogger(__name__)

router = APIRouter()


def _env_flag(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


# The integrated app is a demo host: the deterministic demo threads can be
# re-seeded on demand. Tampering with the append-only audit log stays off
# unless it is explicitly enabled, because it breaks the hash chain on purpose.
RESET_ENABLED = _env_flag("DEMO_RESET_ENABLED", False)
TAMPER_ENABLED = _env_flag("DEMO_TAMPER_ENABLED", False)
DEMO_FAILURE_ENABLED = _env_flag("DEMO_FAILURE_MODE", False)

INDUSTRIES: dict[str, str] = {
    "0000320193": "Technology",
    "0000789019": "Software",
    "0001045810": "Semiconductors",
    "0001318605": "Automotive",
    "0001018724": "E-commerce & Cloud",
}

CONFIDENCE_SIGNAL_KEYS: tuple[str, ...] = (
    "snippet_grounding",
    "concept_match",
    "unit_consistency",
    "period_consistency",
    "prior_year_sanity",
    "extractor_agreement",
    "column_period",
    "cross_statement",
    "llm_confidence",
)

_SEVERITY_TO_UI: dict[str, str] = {
    "HIGH": "HIGH",
    "MEDIUM": "MEDIUM",
    "LOW": "LOW",
    "FAIL": "HIGH",
    "WARNING": "MEDIUM",
    "PASS": "LOW",
    "SKIPPED": "LOW",
}

_TERMINAL_STATUSES = {"COMPLETED", "AUTO_APPROVED"}

_FALLBACK_REASON = (
    "A deterministic validation rule flagged this metric for human review. "
    "Check the source evidence below against the filing before approving it."
)


# ---------------------------------------------------------------------------
# Small conversion helpers
# ---------------------------------------------------------------------------


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name).lower()).strip()


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    number = _as_float(value)
    if number is None:
        return None
    return int(round(number))


def _as_fraction(value: Any) -> float | None:
    """Backend reports percentages on 0-100 scale."""
    number = _as_float(value)
    if number is None:
        return None
    # Clamp to valid percentage range to prevent impossible values
    number = max(0.0, min(1000.0, number))
    return round(number, 6)


def _as_fiscal_year(value: Any) -> int | None:
    """Accept ``2026``, ``"2026"`` and ``"FY2026"``; return the year."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"\d{4}", str(value))
    return int(match.group()) if match else None


def _company_for_cik(cik: str | None) -> dict[str, Any] | None:
    raw = str(cik or "").strip()
    normalized = raw.zfill(10)
    for name, profile in TARGET_COMPANIES.items():
        if str(profile.get("cik") or "").zfill(10) == normalized:
            return {"name": name, **profile}
    return None


def _confidence_signals(raw: Any) -> dict[str, float | None]:
    signals = raw if isinstance(raw, dict) else {}
    return {key: _as_float(signals.get(key)) for key in CONFIDENCE_SIGNAL_KEYS}


def _severity_ui(value: Any) -> str:
    return _SEVERITY_TO_UI.get(str(value or "").upper(), "MEDIUM")


def _reason_sentences(item: dict[str, Any]) -> list[str]:
    """Return at least one clear reason sentence for a review item."""
    reasons: list[str] = []
    for reason in item.get("reasons") or []:
        text = str(reason).strip()
        if text and text not in reasons:
            reasons.append(text)
    rules = [str(rule) for rule in (item.get("rules") or []) if str(rule).strip()]
    if not reasons and rules:
        reasons.append(
            "The "
            + ", ".join(rules)
            + " check did not pass for this metric, so a human reviewer must confirm it."
        )
    if not reasons:
        reasons.append(_FALLBACK_REASON)
    return reasons


# ---------------------------------------------------------------------------
# View builders
# ---------------------------------------------------------------------------


def _metric_view(
    metric: dict[str, Any],
    *,
    decision_state: str,
    reasons: list[str] | None = None,
    rules: list[str] | None = None,
    severity: str | None = None,
    escalation_types: list[str] | None = None,
) -> dict[str, Any]:
    view = {
        "metric_name": str(metric.get("metric_name") or ""),
        "value_usd": _as_float(metric.get("value_usd")) or 0.0,
        "original_value": _as_float(metric.get("original_value")) or 0.0,
        "unit": str(metric.get("unit") or "USD"),
        "period": str(metric.get("period") or ""),
        "prior_year_value_usd": _as_float(metric.get("prior_year_value_usd")),
        "confidence": (
            _as_float(metric.get("confidence_score"))
            or _as_float(metric.get("confidence"))
            or 0.0
        ),
        "confidence_label": str(metric.get("confidence_label") or "LOW"),
        "confidence_signals": _confidence_signals(metric.get("confidence_signals")),
        "source_snippet": str(metric.get("source_snippet") or ""),
        "source_header": str(
            metric.get("source_header")
            or metric.get("source_table_title")
            or ""
        ),
        "source_row": str(
            metric.get("source_row") or metric.get("source_row_text") or ""
        ),
        "decision_state": decision_state,
    }
    if reasons is not None:
        view["reasons"] = reasons
        view["rules"] = rules or []
        view["severity"] = severity or "MEDIUM"
        view["escalation_types"] = escalation_types or []
    return view


def _flagged_views(
    metrics: list[dict[str, Any]],
    flagged_items: list[dict[str, Any]],
    decisions: dict[str, str],
    terminal: bool,
) -> tuple[list[dict[str, Any]], set[str]]:
    flagged: list[dict[str, Any]] = []
    flagged_keys: set[str] = set()
    for item in flagged_items or []:
        name = str(item.get("metric_name") or "")
        key = _norm_name(name)
        flagged_keys.add(key)
        source = item
        for metric in metrics or []:
            if _norm_name(str(metric.get("metric_name") or "")) == key:
                source = metric
                break
        merged = {**source, **{k: v for k, v in item.items() if v is not None}}
        decision_state = decisions.get(key) or ("AUTO_APPROVED" if terminal else "FLAGGED")
        flagged.append(
            _metric_view(
                merged,
                decision_state=decision_state,
                reasons=_reason_sentences(merged),
                rules=[str(rule) for rule in (merged.get("rules") or [])],
                severity=_severity_ui(merged.get("severity")),
                escalation_types=[
                    str(value) for value in types_for_item(merged)
                ],
            )
        )
    return flagged, flagged_keys


def _metric_views(
    metrics: list[dict[str, Any]],
    flagged: list[dict[str, Any]],
    terminal: bool,
) -> list[dict[str, Any]]:
    flagged_keys: set[str] = {_norm_name(str(item.get("metric_name") or "")) for item in flagged}
    views: list[dict[str, Any]] = []
    for metric in metrics or []:
        key = _norm_name(str(metric.get("metric_name") or ""))
        decision_state = (
            "FLAGGED" if key in flagged_keys else ("AUTO_APPROVED" if terminal else "PENDING")
        )
        views.append(_metric_view(metric, decision_state=decision_state))
    return views


def _to_validation_check(check: dict[str, Any]) -> dict[str, Any]:
    details = check.get("details")
    if isinstance(details, dict):
        details_text = "; ".join(
            f"{key}: {value}" for key, value in details.items()
        )
    else:
        details_text = str(details or check.get("reason") or "")
    return {
        "rule_name": str(check.get("rule_name") or ""),
        "metric_name": str(check.get("metric_name") or ""),
        "status": str(check.get("status") or ("PASSED" if check.get("passed") else "FAILED")),
        "reason": str(check.get("reason") or ""),
        "details": details_text,
        "metrics_involved": [
            str(name) for name in (check.get("metrics_involved") or [check.get("metric_name")])
        ],
    }


def _to_anomaly_explanations(
    flagged: list[dict[str, Any]],
    raw: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    explanations: dict[str, dict[str, Any]] = {}
    for item in flagged:
        name = str(item.get("metric_name") or "")
        detail = str((raw or {}).get(name) or "").strip()
        if not detail:
            detail = (
                f"{name} was flagged by the deterministic validation rules. "
                "Verify the current-period line in the filing before approval."
            )
        explanations[name] = {
            "metric_name": name,
            "title": name,
            "detail": detail,
            "severity": str(item.get("severity") or "MEDIUM"),
        }
    return explanations


def _to_accuracy_report(raw: Any) -> dict[str, Any] | None:
    report = raw if isinstance(raw, dict) else {}
    if not report:
        return None

    details = report.get("metric_details") or report.get("original_metric_details") or []
    metric_details: list[dict[str, Any]] = []
    for detail in details or []:
        if not isinstance(detail, dict):
            continue
        extracted = _as_float(detail.get("extracted_value"))
        if extracted is None:
            extracted = _as_float(detail.get("original_value"))
        final = _as_float(detail.get("audited_value"))
        if final is None:
            final = _as_float(detail.get("final_value"))
        xbrl = _as_float(detail.get("xbrl_value"))
        diff_pct = (
            round((final - xbrl) / abs(xbrl) * 100.0, 4)
            if final is not None and xbrl
            else 0.0
        )
        metric_details.append(
            {
                "metric": str(detail.get("metric_name") or detail.get("metric") or ""),
                "extracted": extracted,
                "final": final,
                "xbrl": xbrl,
                "has_ground_truth": xbrl is not None,
                "match": bool(detail.get("match")) if xbrl is not None else None,
                "diff_pct": round(diff_pct, 4) if xbrl is not None else None,
                "confidence": _as_fraction(detail.get("confidence")),
                "confidence_label": detail.get("confidence_label"),
                "escalation_types": [
                    str(value) for value in (detail.get("escalation_types") or [])
                ],
                "note": str(detail.get("note") or ""),
            }
        )

    escalation_by_type: dict[str, Any] = {}
    raw_types = report.get("escalation_precision_by_type")
    if isinstance(raw_types, dict):
        for key, value in raw_types.items():
            raw_value = value.get("precision_pct") if isinstance(value, dict) else value
            escalation_by_type[str(key)] = _as_fraction(raw_value)

    per_metric: list[dict[str, Any]] = []
    for entry in report.get("per_metric_accuracy") or []:
        if not isinstance(entry, dict):
            continue
        per_metric.append(
            {
                "metric_name": str(entry.get("metric_name") or ""),
                "role": str(entry.get("role") or ""),
                "original_match": entry.get("original_match"),
                "final_match": entry.get("final_match"),
                "original_difference": entry.get("original_difference"),
                "final_difference": entry.get("final_difference"),
                "confidence": _as_fraction(entry.get("confidence")),
                "escalation_types": [
                    str(value) for value in (entry.get("escalation_types") or [])
                ],
            }
        )

    return {
        "accuracy_pct": _as_fraction(report.get("accuracy_pct")),
        "original_accuracy_pct": _as_fraction(report.get("original_accuracy_pct")),
        "auto_approval_accuracy_pct": _as_fraction(
            report.get("auto_approval_accuracy_pct")
        ),
        "auto_approved_count": _as_int(report.get("auto_approved_count")) or 0,
        "auto_approved_compared": _as_int(report.get("auto_approved_compared")) or 0,
        "auto_approved_matches": _as_int(report.get("auto_approved_matches")) or 0,
        "false_auto_approvals": _as_int(
            report.get("false_auto_approvals")
            if report.get("false_auto_approvals") is not None
            else report.get("false_auto_approval_count")
        )
        or 0,
        "escalation_rate_pct": _as_fraction(report.get("escalation_rate_pct")),
        "escalated_count": _as_int(report.get("escalated_count")) or 0,
        "escalation_precision_pct": _as_fraction(report.get("escalation_precision_pct")),
        "escalation_precision_by_type": escalation_by_type,
        "escalation_recall_pct": _as_fraction(report.get("escalation_recall_pct")),
        "per_metric_accuracy": per_metric,
        "metric_details": metric_details,
    }


def _decisions_from_state(payload: dict[str, Any]) -> dict[str, str]:
    decisions: dict[str, str] = {}
    for decision in payload.get("human_decisions") or []:
        if not isinstance(decision, dict):
            continue
        key = _norm_name(str(decision.get("metric_name") or ""))
        value = str(decision.get("decision") or "").upper()
        if key and value:
            decisions[key] = value
    return decisions


def _is_terminal(status: Any) -> bool:
    return str(status or "").upper() in _TERMINAL_STATUSES


def _to_status(thread_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    filing = payload.get("filing_metadata") or {}
    metrics = list(payload.get("extracted_metrics") or [])
    flagged_items = list(payload.get("flagged_items") or [])
    decisions = _decisions_from_state(payload)
    terminal = _is_terminal(payload.get("status"))
    flagged, flagged_keys = _flagged_views(metrics, flagged_items, decisions, terminal)
    metric_views = _metric_views(metrics, flagged, terminal)
    for metric in metric_views:
        key = _norm_name(str(metric.get("metric_name") or ""))
        if key in flagged_keys:
            metric["decision_state"] = decisions.get(key) or (
                "AUTO_APPROVED" if terminal else "FLAGGED"
            )

    company = _company_for_cik(str(payload.get("cik") or "")) or {}
    return {
        "thread_id": str(thread_id or payload.get("thread_id") or ""),
        "company_name": str(
            payload.get("company_name") or filing.get("company_name") or company.get("name") or ""
        ),
        "ticker": str(company.get("ticker") or ""),
        "cik": str(payload.get("cik") or filing.get("cik") or ""),
        "fiscal_year": _as_fiscal_year(
            filing.get("fiscal_year")
            if filing.get("fiscal_year") is not None
            else payload.get("fiscal_year")
        )
        or 0,
        "period_end": str(filing.get("report_date") or ""),
        "filing_url": str(filing.get("filing_url") or ""),
        "status": str(payload.get("status") or "UNKNOWN"),
        "metrics": metric_views,
        "validation_checks": [
            _to_validation_check(check) for check in (payload.get("validation_checks") or [])
        ],
        "flagged_items": flagged,
        "anomaly_explanations": _to_anomaly_explanations(
            flagged, payload.get("anomaly_explanations") or {}
        ),
        "accuracy_report": _to_accuracy_report(payload.get("accuracy_report")),
        "thresholds": {
            "confidence_threshold": float(payload.get("confidence_threshold") or 0.85),
            "yoy_change_pct": float(payload.get("yoy_threshold_pct") or 30.0),
            "accounting_tolerance_pct": 0.001,
            "segment_tolerance_pct": 0.001,
        },
        "demo_only": bool(payload.get("demo_only")),
        "side_effect_counts": payload.get("side_effect_counts") or {},
        "created_at": str(payload.get("created_at") or ""),
        "updated_at": str(payload.get("updated_at") or payload.get("created_at") or ""),
    }


def _to_thread_summary(thread_id: str, status: dict[str, Any]) -> dict[str, Any]:
    flagged = list(status.get("flagged_items") or [])
    metrics = list(status.get("metrics") or [])
    accuracy = status.get("accuracy_report") or {}
    decided_states = {"APPROVED", "CORRECTED", "REJECTED"}
    auto_approved_count = sum(
        1 for metric in metrics if metric.get("decision_state") == "AUTO_APPROVED"
    )
    human_reviewed_count = sum(
        1
        for metric in metrics
        if metric.get("decision_state") in decided_states
        or metric.get("decision_state") == "FLAGGED"
    ) - sum(1 for metric in metrics if metric.get("decision_state") == "FLAGGED")
    return {
        "thread_id": str(thread_id or status.get("thread_id") or ""),
        "company_name": str(status.get("company_name") or ""),
        "ticker": str(status.get("ticker") or ""),
        "cik": str(status.get("cik") or ""),
        "fiscal_year": status.get("fiscal_year"),
        "status": str(status.get("status") or "UNKNOWN"),
        "flagged_count": len(flagged),
        "auto_approved_count": auto_approved_count,
        "created_at": str(status.get("created_at") or ""),
        "updated_at": str(status.get("updated_at") or ""),
        "demo_only": bool(status.get("demo_only")),
        "accuracy_pct": _as_float(accuracy.get("accuracy_pct")),
        "original_accuracy_pct": _as_float(accuracy.get("original_accuracy_pct")),
        "auto_approval_accuracy_pct": _as_float(accuracy.get("auto_approval_accuracy_pct")),
        "false_auto_approvals": _as_int(accuracy.get("false_auto_approvals"))
        or _as_int(accuracy.get("false_auto_approval_count"))
        or 0,
        "escalation_rate_pct": _as_float(accuracy.get("escalation_rate_pct")),
        "escalation_precision_pct": _as_float(accuracy.get("escalation_precision_pct")),
        "escalation_recall_pct": _as_float(accuracy.get("escalation_recall_pct")),
        "escalation_precision_by_type": accuracy.get("escalation_precision_by_type") or {},
        "metric_details": accuracy.get("metric_details") or [],
        "human_reviewed_count": human_reviewed_count,
    }


def _to_audit_log_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id") or row.get("row_id") or ""),
        "thread_id": str(row.get("thread_id") or ""),
        "company_name": str(row.get("company_name") or ""),
        "metric_name": str(row.get("metric_name") or ""),
        "decision": str(row.get("decision") or "").upper(),
        "original_value": _as_float(row.get("original_value")) or 0.0,
        "corrected_value": _as_float(row.get("corrected_value")),
        "reviewer_notes": str(row.get("reviewer_notes") or ""),
        "timestamp": str(row.get("timestamp") or ""),
        "prev_hash": str(row.get("prev_hash") or ""),
        "row_hash": str(row.get("row_hash") or ""),
    }


def _to_audit_events(thread_id: str, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for index, snapshot in enumerate(history or []):
        values = snapshot.get("values") or {}
        status = str(values.get("status") or "UNKNOWN")
        events.append(
            {
                "id": str(snapshot.get("checkpoint_id") or f"{thread_id}-{index}"),
                "thread_id": str(thread_id),
                "step": index + 1,
                "event": _step_label(status),
                "status": status,
                "timestamp": str(snapshot.get("created_at") or ""),
                "detail": str(values.get("status") or ""),
            }
        )
    return events


_STEP_LABELS: dict[str, str] = {
    "UNKNOWN": "Audit created",
    "STARTED": "Audit started",
    "FETCHED": "Filing fetched from SEC EDGAR",
    "EXTRACTED": "Metrics extracted from the filing tables",
    "VALIDATED": "Deterministic validation finished",
    "PAUSED_FOR_REVIEW": "Paused for human review",
    "HUMAN_REVIEWED": "Human review recorded",
    "AUTO_APPROVED": "Auto-approved without review",
    "COMPLETED": "Audit completed",
    "FAILED": "Audit failed",
}


def _step_label(status: str) -> str:
    return _STEP_LABELS.get(status.upper(), status or "Step")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ReviewDecisionInput(BaseModel):
    metric_name: str = Field(min_length=1)
    decision: Literal["APPROVED", "CORRECTED", "REJECTED"] = Field(
        description="APPROVED, CORRECTED, or REJECTED."
    )
    corrected_value: float | None = Field(default=None, ge=0.0)
    reviewer_notes: str = ""
    acknowledge_risk: bool = False


class ResumeAuditRequest(BaseModel):
    decisions: list[ReviewDecisionInput] = Field(default_factory=list)


class DemoFailureModeRequest(BaseModel):
    enabled: bool = False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.api_route("/", methods=["GET", "HEAD"])
def trace_root() -> dict[str, Any]:
    """Service index.

    Render's default health check requests ``/``; answering with a small JSON
    index keeps the service healthy and tells a human who opens the URL what
    this API is and where the interactive documentation lives.
    """
    return {
        "service": "TRACE — Financial Filing Auditor API",
        "version": "2.0.0",
        "status": "ok",
        "storage": backend_label(),
        "docs": "/docs",
        "health": "/health",
        "endpoints": {
            "companies": "/companies",
            "threads": "/threads",
            "start_audit": "POST /audits",
            "audit_log": "/audit-logs",
            "verify_chain": "/audit-logs/verify",
        },
    }


@router.api_route("/health", methods=["GET", "HEAD"])
def trace_health() -> dict[str, str]:
    """Liveness probe used by the UI to decide Live vs Demo mode."""
    return {
        "status": "ok",
        "version": "2.0.0",
        "storage": backend_label(),
    }


@router.get("/companies")
def trace_companies() -> list[dict[str, Any]]:
    """The five supported companies with their CIK and industry."""
    companies: list[dict[str, Any]] = []
    for name, profile in TARGET_COMPANIES.items():
        cik = str(profile.get("cik") or "")
        companies.append(
            {
                "name": name,
                "ticker": str(profile.get("ticker") or ""),
                "cik": cik,
                "industry": INDUSTRIES.get(cik.zfill(10), "Public Equity"),
            }
        )
    companies.sort(key=lambda company: company["ticker"])
    return companies


@router.post("/audits")
def trace_start_audit(payload: BackendStartAuditRequest) -> dict[str, Any]:
    """Start an audit and run it until completion or the review interrupt."""
    yoy = float(payload.yoy_threshold_pct or 0.0)
    # The UI exposes tolerances as fractions (0.30 == 30%); the backend expects
    # percentages (30.0). Values at or below 1.0 are treated as fractions.
    if yoy <= 1.0:
        yoy = round(yoy * 100.0, 4)
    request = BackendStartAuditRequest(
        cik=str(payload.cik or ""),
        confidence_threshold=float(payload.confidence_threshold or 0.85),
        yoy_threshold_pct=yoy,
        demo_only=bool(payload.demo_only),
    )
    if DEMO_FAILURE_ENABLED:
        thread_id = f"audit-fail-{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        status = {
            "thread_id": thread_id,
            "status": "FAILED",
            "created_at": now,
            "updated_at": now,
            "company_name": "",
            "cik": str(request.cik),
            "confidence_threshold": request.confidence_threshold,
            "yoy_threshold_pct": request.yoy_threshold_pct,
            "demo_only": request.demo_only,
            "filing_metadata": {},
            "extracted_metrics": [],
            "flagged_items": [],
            "accuracy_report": {},
        }
        FAILURE_THREADS[thread_id] = status
        return _to_status(thread_id, status)

    try:
        response = backend_start_audit(request)
    except HTTPException:
        raise
    except Exception as error:  # noqa: BLE001 - never leak a stack trace
        logger.exception("Audit failed for CIK %s", request.cik)
        raise HTTPException(
            status_code=502,
            detail=(
                "The audit could not be completed. Check that SEC EDGAR is reachable "
                "and that the LLM provider credentials are valid, then try again."
            ),
        ) from error
    return _to_status(str(response.get("thread_id") or ""), response)


@router.get("/threads")
def trace_list_threads() -> list[dict[str, Any]]:
    """Summaries for every known audit thread, newest first."""
    with _GRAPH_LOCK:
        graph = _get_graph()
        summaries: list[dict[str, Any]] = []
        for thread_id in _checkpoint_thread_ids():
            try:
                snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
            except Exception:  # noqa: BLE001 - skip unreadable checkpoints
                logger.warning("Skipping unreadable thread %s", thread_id)
                continue
            status = _to_status(thread_id, _state_response(thread_id, snapshot))
            summaries.append(_to_thread_summary(thread_id, status))
        for thread_id, status in list(FAILURE_THREADS.items()):
            summaries.append(_to_thread_summary(thread_id, _to_status(thread_id, status)))
    summaries.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return summaries


@router.get("/threads/{thread_id}/status")
def trace_thread_status(thread_id: str) -> dict[str, Any]:
    """Current state of one audit thread."""
    if thread_id in FAILURE_THREADS:
        return _to_status(thread_id, FAILURE_THREADS[thread_id])
    with _GRAPH_LOCK:
        graph = _get_graph()
        snapshot = _existing_snapshot(graph, thread_id)
        payload = _state_response(thread_id, snapshot)
    return _to_status(thread_id, payload)


@router.get("/threads/{thread_id}/timeline")
def trace_thread_timeline(thread_id: str) -> list[dict[str, Any]]:
    """Ordered checkpoint history for one audit thread."""
    with _GRAPH_LOCK:
        graph = _get_graph()
        _existing_snapshot(graph, thread_id)
        history = list(graph.get_state_history({"configurable": {"thread_id": thread_id}}))

    events: list[dict[str, Any]] = []
    for index, snapshot in enumerate(reversed(history)):
        values = dict(getattr(snapshot, "values", None) or {})
        status = str(values.get("status") or "UNKNOWN")
        events.append(
            {
                "id": str(
                    (getattr(snapshot, "config", None) or {})
                    .get("configurable", {})
                    .get("checkpoint_id")
                    or f"{thread_id}-{index}"
                ),
                "thread_id": str(thread_id),
                "step": index + 1,
                "event": _step_label(status),
                "status": status,
                "timestamp": str(getattr(snapshot, "created_at", None) or ""),
                "detail": _step_detail(status, values),
            }
        )
    return events


def _step_detail(status: str, values: dict[str, Any]) -> str:
    if status == "UNKNOWN":
        return "The audit thread was created and the workflow was scheduled"
    if status == "PAUSED_FOR_REVIEW":
        flagged = values.get("flagged_items") or []
        names = ", ".join(str(item.get("metric_name") or "") for item in flagged)
        return f"Waiting for a human decision on: {names}" if names else "Waiting for review"
    if status == "COMPLETED":
        report = values.get("accuracy_report") or {}
        accuracy = report.get("accuracy_pct")
        if accuracy is not None:
            return f"Accuracy vs XBRL: {accuracy}%"
        return "Audit finished"
    return status


@router.post("/threads/{thread_id}/resume")
def trace_resume(thread_id: str, request: ResumeAuditRequest) -> dict[str, Any]:
    """Record human decisions and finish the audit."""
    decisions = list(request.decisions or [])
    backend_decisions = {
        _norm_name(decision.metric_name): BackendHumanDecision(
            decision=decision.decision,  # type: ignore[arg-type]
            corrected_value=decision.corrected_value,
            notes=decision.reviewer_notes,
            acknowledge_risk=decision.acknowledge_risk,
        )
        for decision in decisions
        if decision.decision in {"APPROVED", "CORRECTED", "REJECTED"}
    }
    if not backend_decisions:
        raise HTTPException(
            status_code=400,
            detail="No valid review decisions were supplied.",
        )
    try:
        response = backend_resume_audit(
            thread_id, BackendResumeAuditRequest(decisions=backend_decisions)
        )
    except HTTPException:
        raise
    except Exception as error:  # noqa: BLE001
        logger.exception("Resume failed for thread %s", thread_id)
        raise HTTPException(
            status_code=502,
            detail="The review decisions could not be applied. Please try again.",
        ) from error
    return _to_status(thread_id, response)


@router.get("/audit-logs")
def trace_audit_logs(
    thread_id: str | None = None,
    company_name: str | None = None,
    decision: str | None = None,
) -> list[dict[str, Any]]:
    """The append-only audit trail, optionally filtered."""
    decision_filter = str(decision).strip().upper() if decision else None
    if decision_filter is not None and decision_filter not in {
        "AUTO_APPROVED",
        "APPROVED",
        "CORRECTED",
        "REJECTED",
    }:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid decision {decision!r}; expected one of "
                "AUTO_APPROVED, APPROVED, CORRECTED, REJECTED."
            ),
        )
    rows = get_audit_logs(thread_id, decision=decision_filter)
    logs = [_to_audit_log_row(row) for row in rows]
    if company_name:
        needle = str(company_name).strip().lower()
        logs = [row for row in logs if needle in row["company_name"].lower()]
    return logs


@router.get("/audit-logs/verify")
def trace_verify_chain() -> dict[str, Any]:
    """Verify the append-only audit row hash chain."""
    result = verify_audit_chain()
    return {
        "verified": bool(result.get("verified")),
        "message": str(result.get("message") or ""),
        "total_rows": int(result.get("total_rows") or 0),
        "checked_rows": int(result.get("checked_rows") or 0),
        "broken_row_id": str(result.get("broken_row_id") or "") or None,
    }


@router.post("/demo/reset")
def trace_demo_reset() -> dict[str, Any]:
    """Clear every thread."""
    if not RESET_ENABLED:
        raise HTTPException(
            status_code=409, detail="Reset is disabled."
        )
    with _GRAPH_LOCK:
        thread_ids = list(_checkpoint_thread_ids())
    cleared = clear_threads(thread_ids)
    FAILURE_THREADS.clear()
    return {"reset": True, "cleared": cleared, "seeded": []}


@router.api_route("/demo/tamper", methods=["GET", "POST"])
def trace_demo_tamper() -> dict[str, Any]:
    """Insert a synthetic audit row to demonstrate chain verification.

    The UI contract calls this with ``GET``; ``POST`` is accepted too so the
    endpoint can be triggered from a script or a hook.
    """
    if not TAMPER_ENABLED:
        result = verify_audit_chain()
        return {
            "verified": bool(result.get("verified")),
            "message": str(result.get("message") or ""),
            "total_rows": int(result.get("total_rows") or 0),
            "checked_rows": int(result.get("checked_rows") or 0),
            "broken_row_id": None,
        }
    thread_id = f"audit-tamper-{uuid.uuid4().hex}"
    insert_audit_log(
        thread_id=thread_id,
        company_name="Tamper Demonstration",
        cik="0000000000",
        metric_name="Revenue",
        original_value=1.0,
        corrected_value=None,
        decision="APPROVED",
        reason="Tamper demonstration row with synthetic provenance.",
        reviewer_notes="append-only tamper demo",
    )
    result = verify_audit_chain()
    return {
        "verified": bool(result.get("verified")),
        "message": str(result.get("message") or ""),
        "total_rows": int(result.get("total_rows") or 0),
        "checked_rows": int(result.get("checked_rows") or 0),
        "broken_row_id": str(result.get("broken_row_id") or "") or None,
    }


@router.post("/demo/failure-mode")
def trace_demo_failure_mode(request: DemoFailureModeRequest) -> dict[str, Any]:
    """Toggle the synthetic failure mode used for fault-injection demos."""
    global DEMO_FAILURE_ENABLED
    DEMO_FAILURE_ENABLED = bool(request.enabled)
    return {"enabled": DEMO_FAILURE_ENABLED}


# In-memory synthetic failure threads (demo failure simulation only).
FAILURE_THREADS: dict[str, dict[str, Any]] = {}

# Escalation type labels are re-exported so the UI can explain every badge.
ESCALATION_LABELS = dict(ESCALATION_TYPE_LABELS)
