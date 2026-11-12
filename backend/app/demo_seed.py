"""Deterministic demo audit threads for the integrated TRACE backend.

Two threads are seeded so the web UI can be exercised end to end without live
SEC or LLM traffic:

- ``audit-seed-paused``   Apple, paused at human review with two flagged items.
- ``audit-seed-completed`` Microsoft, auto-approved and completed.

The Apple values are the real FY2026 figures reported in the latest 10-K, so
resuming the paused thread produces a meaningful XBRL comparison instead of a
mismatched demo number.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from app.store import clear_threads as clear_threads_in_store
from app.store import insert_audit_log
from app.graph import build_audit_graph, build_checkpointer, get_checkpoint_path
from app.units import normalize_metric_dict

__all__ = [
    "COMPLETED_THREAD_ID",
    "PAUSED_THREAD_ID",
    "clear_threads",
    "seed_demo_threads",
]

PAUSED_THREAD_ID = "audit-seed-paused"
COMPLETED_THREAD_ID = "audit-seed-completed"

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _signals(confidence: float = 0.7, agreement: float = 0.9) -> dict[str, float | None]:
    return {
        "snippet_grounding": 0.8,
        "concept_match": 0.9,
        "unit_consistency": 1.0,
        "period_consistency": 1.0,
        "prior_year_sanity": 0.9,
        "extractor_agreement": agreement,
        "column_period": 1.0,
        "cross_statement": 1.0,
        "llm_confidence": confidence,
    }


def _metric(
    name: str,
    value: float,
    prior: float | None,
    confidence: float,
    *,
    unit: str = "USD Millions",
    period: str = "FY2025",
) -> dict[str, Any]:
    metric = {
        "metric_name": name,
        "value": value,
        "prior_year_value": prior,
        "unit": unit,
        "period": period,
        "source_snippet": f"{name} from the consolidated statements — ${value:,.0f}",
        "confidence": confidence,
    }
    normalize_metric_dict(metric)
    metric["confidence_score"] = confidence
    metric["confidence_signals"] = _signals(confidence)
    metric["confidence_label"] = "HIGH" if confidence >= 0.85 else "LOW"
    metric["decision_state"] = "FLAGGED"
    return metric


PAUSED_METRICS = [
    _metric("Revenue", 478834.0, 416161.0, 0.78, period="FY2026"),
    _metric("Net Income", 132175.0, 112010.0, 0.96, period="FY2026"),
    _metric("Total Assets", 404548.0, 359241.0, 0.93, period="FY2026"),
    _metric("Total Liabilities", 292455.0, 285508.0, 0.9, period="FY2026"),
    _metric("Shareholders' Equity", 112093.0, 73733.0, 0.88, period="FY2026"),
    _metric("Operating Cash Flow", 145097.0, 111482.0, 0.91, period="FY2026"),
    _metric("Cash and Cash Equivalents", 36305.0, 35934.0, 0.89, period="FY2026"),
    _metric("Research and Development Expense", 45765.0, 34550.0, 0.9, period="FY2026"),
]

COMPLETED_METRICS = [
    _metric("Revenue", 434600.0, 383300.0, 0.97),
    _metric("Net Income", 112300.0, 88700.0, 0.96),
    _metric("Total Assets", 396400.0, 365200.0, 0.93),
    _metric("Total Liabilities", 304400.0, 283800.0, 0.9),
    _metric("Shareholders' Equity", 92600.0, 81000.0, 0.88),
    _metric("Operating Cash Flow", 118300.0, 108800.0, 0.91),
    _metric("Cash and Cash Equivalents", 61100.0, 55200.0, 0.89),
    _metric("Research and Development Expense", 31200.0, 26900.0, 0.9),
]


def _flagged(
    metric: dict[str, Any],
    severity: str,
    escalation_types: list[str],
    reasons: list[str],
    rules: list[str],
) -> dict[str, Any]:
    item = dict(metric)
    item["reasons"] = reasons
    item["rules"] = rules
    item["severity"] = severity
    item["escalation_types"] = escalation_types
    item["decision_state"] = "FLAGGED"
    return item


def _paused_state() -> dict[str, Any]:
    return {
        "thread_id": PAUSED_THREAD_ID,
        "cik": "0000320193",
        "company_name": "Apple Inc.",
        "confidence_threshold": 0.85,
        "yoy_threshold_pct": 30.0,
        "tables_markdown": [],
        "filing_metadata": {
            "company_name": "Apple Inc.",
            "cik": "0000320193",
            "fiscal_year": "FY2026",
            "report_date": "2026-09-26",
            "filing_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019326000026/aapl-20260926.htm",
        },
        "extracted_metrics": [dict(metric) for metric in PAUSED_METRICS],
        "original_metrics": [dict(metric) for metric in PAUSED_METRICS],
        "validation_checks": [],
        "flagged_items": [
            _flagged(
                PAUSED_METRICS[0],
                "HIGH",
                ["EXTRACTION_RISK", "FINANCIAL_ANOMALY"],
                [
                    "Revenue was matched to a secondary statement rather than the "
                    "primary consolidated statement of operations.",
                    "The source snippet does not contain every digit of the reported "
                    "revenue figure, so the value could not be fully grounded.",
                ],
                ["CONCEPT_SYNONYM", "SNIPPET_BOUNDARY"],
            ),
            _flagged(
                PAUSED_METRICS[5],
                "MEDIUM",
                ["FINANCIAL_ANOMALY"],
                [
                    "Operating cash flow moved more than the configured year-over-year "
                    "tolerance, so the change needs a human look.",
                ],
                ["YoY Analysis"],
            ),
        ],
        "anomaly_explanations": {},
        "human_decisions": [],
        "accuracy_report": {},
        "routing_decision": "ESCALATE",
        "demo_only": False,
        "status": "PAUSED_FOR_REVIEW",
    }


def _completed_state() -> dict[str, Any]:
    return {
        "thread_id": COMPLETED_THREAD_ID,
        "cik": "0000789019",
        "company_name": "Microsoft",
        "confidence_threshold": 0.85,
        "yoy_threshold_pct": 30.0,
        "tables_markdown": [],
        "filing_metadata": {
            "company_name": "Microsoft",
            "cik": "0000789019",
            "fiscal_year": 2025,
            "report_date": "2025-08-28",
            "filing_url": "https://www.sec.gov/Archives/edgar/data/789019/000078901925000106/msft-20250828.htm",
        },
        "extracted_metrics": [dict(metric) for metric in COMPLETED_METRICS],
        "original_metrics": [dict(metric) for metric in COMPLETED_METRICS],
        "validation_checks": [],
        "flagged_items": [],
        "anomaly_explanations": {},
        "human_decisions": [
            {
                "thread_id": COMPLETED_THREAD_ID,
                "company_name": "Microsoft",
                "cik": "0000789019",
                "metric_name": "Revenue",
                "original_value": 245500.0,
                "corrected_value": None,
                "decision": "AUTO_APPROVED",
                "reason": "Passed deterministic validation with no review flags.",
                "reviewer_notes": "Automatically approved; no human review required.",
            }
        ],
        "accuracy_report": {
            "thread_id": COMPLETED_THREAD_ID,
            "company_name": "Microsoft",
            "cik": "0000789019",
            "fiscal_year": 2025,
            "period_end": "2025-08-28",
            "tolerance_pct": 1.0,
            "total_values": 8,
            "metrics_compared": 8,
            "matches": 7,
            "accuracy_pct": 87.5,
            "final_accuracy_pct": 87.5,
            "original_metrics_compared": 8,
            "original_matches": 8,
            "original_accuracy_pct": 100.0,
            "metric_details": [],
            "per_metric_accuracy": [],
            "auto_approved_count": 1,
            "escalated_count": 0,
            "flagged_item_count": 0,
            "human_reviewed_count": 0,
            "approved_count": 0,
            "corrected_count": 0,
            "rejected_count": 0,
            "escalation_precision_pct": None,
            "escalation_precision_by_type": {},
            "escalation_recall_pct": None,
            "genuine_escalations": 0,
            "genuine_issues": 0,
            "auto_approval_accuracy_pct": 100.0,
            "auto_approved_compared": 1,
            "auto_approved_matches": 1,
            "unresolved_flagged_count": 0,
            "audit_log_count": 1,
            "demo_only": False,
            "headline_eligible": True,
            "generated_at": "2026-11-12T00:00:00+00:00",
        },
        "routing_decision": "AUTO_APPROVE",
        "demo_only": False,
        "status": "COMPLETED",
    }


def clear_threads(
    thread_ids: list[str],
    checkpoint_db: str | None = None,
) -> int:
    """Delete the checkpoints of the given threads from the checkpoint store.

    The append-only audit log is never touched: only the resumable workflow
    state is discarded so a demo can start from a clean slate.
    """
    if checkpoint_db:
        # A specific SQLite file was requested: use it directly.
        if not thread_ids:
            return 0
        path = Path(checkpoint_db)
        if not path.exists():
            return 0
        connection = sqlite3.connect(str(path), timeout=30.0)
        try:
            placeholders = ",".join("?" for _ in thread_ids)
            removed = connection.execute(
                f"DELETE FROM checkpoints WHERE thread_id IN ({placeholders})", thread_ids
            ).rowcount
            connection.execute(
                f"DELETE FROM writes WHERE thread_id IN ({placeholders})", thread_ids
            )
            connection.commit()
        finally:
            connection.close()
        return removed
    return clear_threads_in_store(thread_ids)


def seed_demo_threads(
    checkpoint_db: str | None = None,
    auditor_db: str | None = None,
) -> list[str]:
    """Write the demo threads into the checkpoint store and audit log.

    Returns the seeded thread ids. The audit-log insert is idempotent, so
    calling this repeatedly does not duplicate rows.
    """
    if auditor_db:
        os.environ["AUDITOR_DB_PATH"] = auditor_db

    db_path = checkpoint_db or os.getenv("AUDITOR_CHECKPOINT_DB") or str(
        _PROJECT_ROOT / "data" / "checkpoints.db"
    )
    connection, checkpointer = build_checkpointer(db_path)
    graph = build_audit_graph(checkpointer=checkpointer)
    try:
        graph.update_state(
            {"configurable": {"thread_id": PAUSED_THREAD_ID}}, _paused_state()
        )
        graph.update_state(
            {"configurable": {"thread_id": PAUSED_THREAD_ID}},
            None,
            as_node="human_review_node",
        )

        graph.update_state(
            {"configurable": {"thread_id": COMPLETED_THREAD_ID}}, _completed_state()
        )
        graph.update_state(
            {"configurable": {"thread_id": COMPLETED_THREAD_ID}},
            None,
            as_node="finalize_node",
        )
    finally:
        connection.close()

    metric = COMPLETED_METRICS[0]
    insert_audit_log(
        thread_id=COMPLETED_THREAD_ID,
        company_name="Microsoft",
        cik="0000789019",
        metric_name=str(metric["metric_name"]),
        original_value=float(metric.get("original_value") or metric.get("value") or 0.0),
        corrected_value=None,
        decision="AUTO_APPROVED",
        reason="Passed deterministic validation with no review flags.",
        reviewer_notes="Automatically approved; no human review required.",
    )

    return [PAUSED_THREAD_ID, COMPLETED_THREAD_ID]
