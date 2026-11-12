"""MongoDB storage tests.

These run against an in-process MongoDB mock, so the MongoDB code path is
verified in CI without a server.  Set ``MONGO_URI`` to a real cluster to run
the same assertions against MongoDB itself.
"""

from __future__ import annotations

import pytest

mongomock = pytest.importorskip("mongomock")

from app import mongo_client, mongo_database, store  # noqa: E402


@pytest.fixture()
def mongo(monkeypatch):
    """Point the Mongo client at an in-process mock database."""
    client = mongomock.MongoClient()
    monkeypatch.setattr(mongo_client, "_client", client)
    monkeypatch.setenv("MONGO_URI", "mongodb://mock/trace")
    monkeypatch.setenv("MONGO_DB_NAME", "trace")
    mongo_database._indexes_ready.clear()
    yield client
    mongo_client.reset_client()
    mongo_database._indexes_ready.clear()


def _insert(**overrides):
    payload = {
        "thread_id": "audit-test",
        "company_name": "Apple Inc.",
        "cik": "0000320193",
        "metric_name": "Revenue",
        "original_value": 478834.0,
        "corrected_value": None,
        "decision": "APPROVED",
        "reason": "Passed deterministic validation.",
        "reviewer_notes": "",
    }
    payload.update(overrides)
    return store.insert_audit_log(**payload)


def test_store_uses_mongodb_when_uri_is_set(mongo) -> None:
    assert store.using_mongodb() is True
    assert "MongoDB" in store.backend_label()


def test_store_uses_sqlite_without_uri(monkeypatch) -> None:
    monkeypatch.delenv("MONGO_URI", raising=False)
    assert store.using_mongodb() is False
    assert "SQLite" in store.backend_label()


def test_audit_rows_are_appended_and_ordered(mongo) -> None:
    first = _insert(metric_name="Revenue")
    second = _insert(metric_name="Net Income", decision="CORRECTED")

    rows = store.get_audit_logs()
    assert len(rows) == 2
    assert [row["metric_name"] for row in rows] == ["Revenue", "Net Income"]
    assert rows[0]["id"] == first
    assert rows[1]["id"] == second
    assert rows[0]["decision"] == "APPROVED"
    assert rows[1]["decision"] == "CORRECTED"


def test_audit_rows_are_idempotent_per_thread_metric_decision(mongo) -> None:
    first = _insert()
    again = _insert()
    assert first == again
    assert len(store.get_audit_logs()) == 1


def test_audit_chain_verifies_and_links_rows(mongo) -> None:
    _insert(metric_name="Revenue")
    _insert(metric_name="Net Income")
    _insert(metric_name="Total Assets")

    result = store.verify_audit_chain()
    assert result["verified"] is True
    assert result["message"] == "Audit chain intact."
    assert result["total_rows"] == 3
    assert result["checked_rows"] == 3

    rows = store.get_audit_logs()
    assert rows[0]["prev_hash"] == "GENESIS"
    assert rows[1]["prev_hash"] == rows[0]["row_hash"]
    assert rows[2]["prev_hash"] == rows[1]["row_hash"]


def test_tampering_with_a_row_is_detected(mongo) -> None:
    _insert(metric_name="Revenue")
    _insert(metric_name="Net Income")

    # Rewrite a stored value behind the application's back.
    mongo["trace"]["audit_logs"].update_one(
        {"metric_name": "Revenue"}, {"$set": {"original_value": 1.0}}
    )

    result = store.verify_audit_chain()
    assert result["verified"] is False
    assert "broken" in result["message"].lower()
    assert result["broken_row_id"] is not None


def test_filters_match_the_sqlite_contract(mongo) -> None:
    _insert(thread_id="audit-a", metric_name="Revenue")
    _insert(thread_id="audit-b", metric_name="Net Income", decision="REJECTED")

    assert len(store.get_audit_logs("audit-a")) == 1
    assert len(store.get_audit_logs(decision="REJECTED")) == 1
    assert len(store.get_audit_logs()) == 2
    assert store.audit_log_exists("audit-a", "Revenue") is True
    assert store.audit_log_exists("audit-a", "Net Income") is False
    assert store.audit_log_exists("audit-b", "Net Income", "REJECTED") is True


def test_invalid_decision_is_rejected(mongo) -> None:
    with pytest.raises(ValueError):
        _insert(decision="MAYBE")


def test_mongo_and_sqlite_hash_the_same_row(mongo) -> None:
    """The Mongo row hash must equal the SQLite row hash for the same record."""
    from app.database import _compute_row_hash

    row = {
        "thread_id": "audit-x",
        "company_name": "Apple Inc.",
        "cik": "0000320193",
        "metric_name": "Revenue",
        "original_value": 478834.0,
        "corrected_value": None,
        "decision": "APPROVED",
        "reason": "Passed deterministic validation.",
        "reviewer_notes": "",
        "timestamp": "2026-01-01T00:00:00.000000+00:00",
    }
    expected = _compute_row_hash(row, "GENESIS")

    mongo_database.insert_audit_log(
        thread_id=row["thread_id"],
        company_name=row["company_name"],
        cik=row["cik"],
        metric_name=row["metric_name"],
        original_value=row["original_value"],
        corrected_value=row["corrected_value"],
        decision=row["decision"],
        reason=row["reason"],
        reviewer_notes=row["reviewer_notes"],
        timestamp=row["timestamp"],
    )
    stored = store.get_audit_logs()[0]
    assert stored["row_hash"] == expected
