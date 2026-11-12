"""Backend integration tests for the combined TRACE + FastAPI app."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app.api import app


PAUSED_THREAD_ID = "audit-seed-paused"
COMPLETED_THREAD_ID = "audit-seed-completed"


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("AUDITOR_DB_PATH", str(tmp_path / "auditor.db"))
    monkeypatch.setenv("AUDITOR_CHECKPOINT_DB", str(tmp_path / "checkpoints.db"))
    monkeypatch.setattr(app_main, "_GRAPH", None)
    monkeypatch.setattr(app_main, "_CHECKPOINTER_CONNECTION", None)
    app_main.initialize_database()
    yield TestClient(app)
    app_main._GRAPH = None
    app_main._CHECKPOINTER_CONNECTION = None


def _seed_paused_thread() -> None:
    graph = app_main._get_graph()
    graph.update_state(
        {"configurable": {"thread_id": PAUSED_THREAD_ID}},
        {
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
            },
            "extracted_metrics": [
                {
                    "metric_name": "Revenue",
                    "value": 478834.0,
                    "prior_year_value": 416161.0,
                    "unit": "USD Millions",
                    "period": "FY2026",
                    "source_snippet": "Revenue from Contracts with Customers — $478,834",
                    "confidence": 0.78,
                    "confidence_score": 0.6,
                    "confidence_signals": {
                        "snippet_grounding": 0.8,
                        "concept_match": 0.9,
                        "unit_consistency": 1.0,
                        "period_consistency": 1.0,
                        "prior_year_sanity": 0.9,
                        "extractor_agreement": 0.9,
                        "column_period": 1.0,
                        "cross_statement": 1.0,
                        "llm_confidence": 0.78,
                    },
                    "confidence_label": "LOW",
                    "decision_state": "FLAGGED",
                }
            ],
            "original_metrics": [],
            "validation_checks": [],
            "flagged_items": [
                {
                    "metric_name": "Revenue",
                    "value": 478834.0,
                    "original_value": 478834.0,
                    "unit": "USD Millions",
                    "period": "FY2026",
                    "severity": "HIGH",
                    "rules": ["Snippet Grounding"],
                    "reasons": ["The reported digits are not fully present in the source row."],
                    "confidence": 0.6,
                    "confidence_label": "LOW",
                    "confidence_signals": {},
                    "escalation_types": ["EXTRACTION_RISK"],
                    "decision_state": "FLAGGED",
                }
            ],
            "anomaly_explanations": {},
            "human_decisions": [],
            "accuracy_report": {},
            "routing_decision": "ESCALATE",
            "demo_only": False,
            "status": "PAUSED_FOR_REVIEW",
        },
    )
    graph.update_state(
        {"configurable": {"thread_id": PAUSED_THREAD_ID}},
        None,
        as_node="human_review_node",
    )


def _seed_completed_thread() -> None:
    graph = app_main._get_graph()
    graph.update_state(
        {"configurable": {"thread_id": COMPLETED_THREAD_ID}},
        {
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
            },
            "extracted_metrics": [],
            "original_metrics": [],
            "validation_checks": [],
            "flagged_items": [],
            "anomaly_explanations": {},
            "human_decisions": [],
            "accuracy_report": {},
            "routing_decision": "AUTO_APPROVE",
            "demo_only": False,
            "status": "COMPLETED",
        },
    )
    graph.update_state(
        {"configurable": {"thread_id": COMPLETED_THREAD_ID}},
        None,
        as_node="finalize_node",
    )


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert isinstance(body.get("version"), str) and body["version"]


def test_companies_returns_ui_catalog(client: TestClient) -> None:
    response = client.get("/companies")
    assert response.status_code == 200
    companies = response.json()
    assert isinstance(companies, list)
    assert len(companies) == 5
    tickers = {company["ticker"] for company in companies}
    assert tickers == {"AAPL", "MSFT", "NVDA", "TSLA", "AMZN"}
    for company in companies:
        assert set(company) == {"name", "ticker", "cik", "industry"}
        assert len(str(company["cik"])) == 10


def test_demo_failure_mode_and_failed_thread(client: TestClient) -> None:
    assert client.post("/demo/failure-mode", json={"enabled": True}).json()["enabled"] is True
    started = client.post(
        "/audits",
        json={
            "cik": "0000320193",
            "confidence_threshold": 0.85,
            "yoy_threshold_pct": 30.0,
            "demo_only": False,
        },
    )
    assert started.status_code == 200
    failed_status = started.json()
    assert failed_status["status"] == "FAILED"
    thread_id = failed_status["thread_id"]
    assert client.get(f"/threads/{thread_id}/status").json()["status"] == "FAILED"
    threads = client.get("/threads").json()
    assert any(thread["thread_id"] == thread_id for thread in threads)

    assert client.post("/demo/failure-mode", json={"enabled": False}).json()["enabled"] is False


def test_paused_thread_contract(client: TestClient) -> None:
    _seed_paused_thread()
    response = client.get(f"/threads/{PAUSED_THREAD_ID}/status")
    assert response.status_code == 200
    status = response.json()
    assert status["thread_id"] == PAUSED_THREAD_ID
    assert status["status"] == "PAUSED_FOR_REVIEW"
    assert status["ticker"] == "AAPL"
    assert status["fiscal_year"] == 2026
    assert status["period_end"] == "2026-09-26"
    assert len(status["metrics"]) == 1
    assert status["metrics"][0]["decision_state"] == "FLAGGED"
    assert status["metrics"][0]["confidence_label"] == "LOW"
    assert set(status["metrics"][0]["confidence_signals"]) >= {
        "snippet_grounding",
        "concept_match",
        "unit_consistency",
        "period_consistency",
        "prior_year_sanity",
        "extractor_agreement",
        "column_period",
        "cross_statement",
        "llm_confidence",
    }
    assert len(status["flagged_items"]) == 1
    assert status["flagged_items"][0]["decision_state"] == "FLAGGED"
    assert status["flagged_items"][0]["escalation_types"] == ["EXTRACTION_RISK"]


def test_flagged_items_always_have_a_reason(client: TestClient) -> None:
    """A reviewer must never see an empty 'Why this was escalated' panel."""
    from app import trace_contract

    view = trace_contract._metric_view(
        {"metric_name": "Revenue"},
        decision_state="FLAGGED",
        reasons=trace_contract._reason_sentences({"rules": ["YoY Analysis"]}),
        rules=["YoY Analysis"],
        severity="MEDIUM",
        escalation_types=["FINANCIAL_ANOMALY"],
    )
    assert view["reasons"], "flagged item must carry a reason sentence"
    assert all(reason.strip() for reason in view["reasons"])

    fallback = trace_contract._reason_sentences({})
    assert fallback and fallback[0].strip()


def test_audit_log_and_chain_contract(client: TestClient) -> None:
    from app.database import insert_audit_log, verify_audit_chain

    insert_audit_log(
        thread_id=COMPLETED_THREAD_ID,
        company_name="Microsoft",
        cik="0000789019",
        metric_name="Revenue",
        original_value=245500.0,
        corrected_value=None,
        decision="AUTO_APPROVED",
        reason="Passed deterministic validation with no review flags.",
        reviewer_notes="Automatically approved; no human review required.",
    )
    verify = client.get("/audit-logs/verify")
    assert verify.status_code == 200
    assert verify.json()["verified"] is True

    logs = client.get("/audit-logs")
    assert logs.status_code == 200
    rows = logs.json()
    assert len(rows) == 1
    assert rows[0]["metric_name"] == "Revenue"
    assert rows[0]["decision"] == "AUTO_APPROVED"
    assert rows[0]["thread_id"] == COMPLETED_THREAD_ID

    filtered = client.get("/audit-logs", params={"decision": "AUTO_APPROVED"})
    assert len(filtered.json()) == 1
    assert client.get("/audit-logs", params={"decision": "NOPE"}).status_code == 400


def test_resume_requires_paused_thread(client: TestClient) -> None:
    _seed_completed_thread()
    response = client.post(
        f"/threads/{COMPLETED_THREAD_ID}/resume",
        json={
            "decisions": [
                {
                    "metric_name": "Revenue",
                    "decision": "APPROVED",
                    "corrected_value": None,
                    "reviewer_notes": "",
                }
            ]
        },
    )
    assert response.status_code == 409
