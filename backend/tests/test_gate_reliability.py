"""Gate 3 reliability checks: append-only triggers, WAL, idempotency, tamper detection."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import database
from app import graph as audit_graph


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "auditor.db"
    database.initialize_database(path)
    return path


def test_audit_log_is_append_only(db):
    database.insert_audit_log(
        thread_id="T1",
        company_name="Test Co",
        cik="0000000001",
        metric_name="Revenue",
        original_value=1.0,
        corrected_value=None,
        decision="APPROVED",
        reason="test",
        reviewer_notes="",
        db_path=db,
    )
    conn = sqlite3.connect(str(db))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE audit_logs SET decision = 'REJECTED' WHERE thread_id = 'T1'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM audit_logs WHERE thread_id = 'T1'")
    finally:
        conn.close()


def test_wal_mode_enabled(db):
    conn = sqlite3.connect(str(db))
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert mode.lower() == "wal"


def test_verify_fails_after_tamper(db):
    database.insert_audit_log(
        thread_id="T1",
        company_name="Test Co",
        cik="0000000001",
        metric_name="Revenue",
        original_value=1.0,
        corrected_value=None,
        decision="APPROVED",
        reason="test",
        reviewer_notes="",
        db_path=db,
    )
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DROP TRIGGER prevent_audit_update")
        conn.execute("UPDATE audit_logs SET original_value = 99.0 WHERE thread_id = 'T1'")
        conn.commit()
    finally:
        conn.close()
    result = database.verify_audit_chain(db)
    assert result["verified"] is False


def test_duplicate_metric_decision_is_idempotent(db):
    ids = [
        database.insert_audit_log(
            thread_id="T1",
            company_name="Test Co",
            cik="0000000001",
            metric_name="Revenue",
            original_value=1.0,
            corrected_value=None,
            decision="APPROVED",
            reason="test",
            reviewer_notes="",
            db_path=db,
        )
        for _ in range(2)
    ]
    conn = sqlite3.connect(str(db))
    try:
        count = conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
    finally:
        conn.close()
    assert count == 1, "double-submitted decision must not duplicate the audit row"
    assert ids[0] == ids[1]
    assert database.verify_audit_chain(db)["verified"] is True


def test_five_companies_can_write_audit_rows_in_parallel(db):
    tickers = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN"]
    metrics = [
        "Revenue",
        "Net Income",
        "Total Assets",
        "Total Liabilities",
        "Shareholders' Equity",
        "Operating Cash Flow",
        "Cash and Cash Equivalents",
        "Research and Development Expense",
    ]

    def write_company(ticker: str) -> None:
        for metric in metrics:
            database.insert_audit_log(
                thread_id=f"audit-{ticker}",
                company_name=ticker,
                cik="0000000001",
                metric_name=metric,
                original_value=1.0,
                corrected_value=None,
                decision="AUTO_APPROVED",
                reason="parallel reliability test",
                db_path=db,
            )

    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(write_company, tickers))

    conn = sqlite3.connect(str(db))
    try:
        count = conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert count == 40
    assert mode.lower() == "wal"
    assert database.verify_audit_chain(db)["verified"] is True


def test_nodes_skip_side_effects_when_checkpoint_already_has_result():
    """Killing after a step and resuming must not repeat that step's work."""
    assert audit_graph.fetch_node({"tables_markdown": [{"markdown": "cached"}]}) == {}
    assert audit_graph.extract_node(
        {"extracted_metrics": [{"metric_name": "Revenue", "value": 1.0}]}
    ) == {}
    assert audit_graph.validate_node(
        {"validation_checks": [{"rule_name": "Snippet Grounding"}]}
    ) == {}


def test_red_tier_approval_is_blocked_without_guard(db, monkeypatch):
    monkeypatch.setenv("AUDITOR_DB_PATH", str(db))
    state = {
        "thread_id": "audit-red",
        "company_name": "Red Co",
        "cik": "0000000001",
        "flagged_items": [
            {
                "metric_name": "Total Assets",
                "value": 100.0,
                "severity": "FAIL",
                "reasons": ["Accounting equation failed."],
                "rules": ["Accounting Equation"],
            }
        ],
        "extracted_metrics": [
            {
                "metric_name": "Total Assets",
                "value": 100.0,
                "original_value": 100.0,
            }
        ],
    }
    monkeypatch.setattr(
        audit_graph,
        "interrupt",
        lambda payload: {"Total Assets": {"decision": "APPROVED"}},
    )
    with pytest.raises(ValueError, match="red-tier"):
        audit_graph.human_review_node(state)

    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0] == 0
    finally:
        conn.close()

    monkeypatch.setattr(
        audit_graph,
        "interrupt",
        lambda payload: {
            "Total Assets": {"decision": "APPROVED", "acknowledge_risk": True}
        },
    )
    result = audit_graph.human_review_node(state)
    assert result["status"] == "HUMAN_REVIEWED"

    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT decision, original_value FROM audit_logs WHERE thread_id = 'audit-red'"
        ).fetchone()
    finally:
        conn.close()
    assert row == ("APPROVED", 100.0)


def test_finalize_replay_keeps_side_effect_counts_at_one(db, monkeypatch):
    monkeypatch.setenv("AUDITOR_DB_PATH", str(db))
    monkeypatch.setattr(
        audit_graph,
        "fetch_xbrl_ground_truth",
        lambda cik: {
            "metrics": {},
            "fiscal_year": "FY2025",
            "period_end": "2025-09-27",
            "cik": cik,
        },
    )
    state = {
        "thread_id": "audit-replay",
        "company_name": "Replay Co",
        "cik": "0000000001",
        "tables_markdown": [{"markdown": "Revenue 1"}],
        "extracted_metrics": [
            {
                "metric_name": "Revenue",
                "value": 1.0,
                "original_value": 1.0,
                "unit": "USD Millions",
                "value_usd": 1_000_000.0,
            }
        ],
        "original_metrics": [
            {
                "metric_name": "Revenue",
                "value": 1.0,
                "original_value": 1.0,
                "unit": "USD Millions",
                "value_usd": 1_000_000.0,
            }
        ],
        "validation_checks": [{"rule_name": "Snippet Grounding", "passed": True}],
        "flagged_items": [],
        "filing_metadata": {"fiscal_year": "FY2025"},
    }

    first = audit_graph.finalize_node(state)
    second = audit_graph.finalize_node(state)

    expected = {
        "fetch_edgar": 1,
        "extract_metrics": 1,
        "validate": 1,
        "persist_audit_decisions": 1,
        "fetch_xbrl_truth": 1,
        "write_accuracy_report": 1,
        "finalize": 1,
    }
    assert first["side_effect_counts"] == expected
    assert second["side_effect_counts"] == expected

    conn = sqlite3.connect(str(db))
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE thread_id = 'audit-replay'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1, "resuming/finalizing twice must not duplicate audit rows"
