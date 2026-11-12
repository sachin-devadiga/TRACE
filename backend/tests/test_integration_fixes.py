"""Regression tests for the integration fixes.

Covers the bugs that broke the live dashboard:

- the LLM response schema must stay provider-safe (no free-form objects);
- percentages reported by the backend must reach the UI as fractions;
- ``POST /demo/reset`` must re-seed the deterministic demo threads.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app.api import app
from app.schemas import ExtractedMetric, LLMExtractionBatch


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


def _schema_nodes(node: dict) -> list[dict]:
    nodes = [node]
    for value in node.values():
        if isinstance(value, dict):
            nodes.extend(_schema_nodes(value))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    nodes.extend(_schema_nodes(item))
    return nodes


def test_llm_schema_is_provider_safe() -> None:
    """Gemini rejects free-form objects; Groq requires additionalProperties:false."""
    schema = LLMExtractionBatch.model_json_schema()
    for node in _schema_nodes(schema):
        if "additionalProperties" in node:
            assert node["additionalProperties"] is False, (
                "free-form objects are rejected by Gemini and Groq"
            )
        if node.get("type") == "object" and "properties" not in node:
            raise AssertionError("schema contains a free-form object")


def test_provider_schema_excludes_free_form_fields() -> None:
    """The provider-facing schema must not contain free-form dict fields."""
    schema = LLMExtractionBatch.model_json_schema()
    metric = schema["$defs"]["LLMExtractedMetric"]
    assert set(metric["properties"]) == {
        "metric_name",
        "value",
        "prior_year_value",
        "unit",
        "period",
        "source_snippet",
        "confidence",
    }
    assert "confidence_signals" not in metric["properties"]
    assert "extractor_agreement_details" not in metric["properties"]
    assert "confidence_signals" in ExtractedMetric.model_fields


def test_accuracy_percentages_are_exposed_as_fractions(client: TestClient) -> None:
    from app import trace_contract

    report = trace_contract._to_accuracy_report(
        {
            "accuracy_pct": 87.5,
            "original_accuracy_pct": 100.0,
            "auto_approval_accuracy_pct": 50.0,
            "escalation_precision_pct": 100.0,
            "escalation_recall_pct": 25.0,
            "escalation_precision_by_type": {"FINANCIAL_ANOMALY": 100.0},
        }
    )
    assert report is not None
    assert report["accuracy_pct"] == pytest.approx(0.875)
    assert report["original_accuracy_pct"] == pytest.approx(1.0)
    assert report["auto_approval_accuracy_pct"] == pytest.approx(0.5)
    assert report["escalation_precision_pct"] == pytest.approx(1.0)
    assert report["escalation_recall_pct"] == pytest.approx(0.25)
    assert report["escalation_precision_by_type"]["FINANCIAL_ANOMALY"] == pytest.approx(1.0)


def test_fiscal_year_accepts_fy_prefix() -> None:
    from app import trace_contract

    assert trace_contract._as_fiscal_year("FY2026") == 2026
    assert trace_contract._as_fiscal_year(2025) == 2025
    assert trace_contract._as_fiscal_year(None) is None


def test_demo_reset_reseeds_deterministic_threads(client: TestClient) -> None:
    reset = client.post("/demo/reset")
    assert reset.status_code == 200
    body = reset.json()
    assert body["reset"] is True
    assert "audit-seed-paused" in body["seeded"]

    threads = {thread["thread_id"]: thread for thread in client.get("/threads").json()}
    assert threads["audit-seed-paused"]["status"] == "PAUSED_FOR_REVIEW"
    assert threads["audit-seed-paused"]["flagged_count"] == 2
    assert threads["audit-seed-completed"]["status"] == "COMPLETED"
    assert threads["audit-seed-completed"]["accuracy_pct"] == pytest.approx(0.875)

    # Resetting twice must be safe and keep the audit chain intact.
    assert client.post("/demo/reset").status_code == 200
    verify = client.get("/audit-logs/verify").json()
    assert verify["verified"] is True


def test_seed_threads_carry_review_reasons(client: TestClient) -> None:
    """The seeded paused thread must explain why each item was escalated."""
    client.post("/demo/reset")
    status = client.get("/threads/audit-seed-paused/status").json()
    flagged = status["flagged_items"]
    assert flagged, "the seeded thread must have flagged items"
    for item in flagged:
        assert item["reasons"], f"{item['metric_name']} is missing a reason"
        assert item["severity"] in {"HIGH", "MEDIUM", "LOW"}
        assert item["escalation_types"], f"{item['metric_name']} is missing an escalation type"
