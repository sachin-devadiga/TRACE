"""MongoDB audit log for TRACE.

This is the MongoDB twin of :mod:`app.database`.  It keeps exactly the same
contract — same function names, same arguments, same hash chain — so the
application does not change when ``MONGO_URI`` is set:

- records are **append-only**: there is no update or delete path here;
- each record carries ``prev_hash`` / ``row_hash`` and is chained to its
  predecessor, so tampering is detected by :func:`verify_audit_chain`;
- records are idempotent per ``(thread_id, metric_name, decision)``.

The chain head lives in a single ``chain_head`` document and the sequence
number in a ``counters`` document, both updated atomically.  A process-level
lock serialises writers inside one instance; deploy a single backend instance
(or a distributed lock) if you run several.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.database import (
    AUDIT_DECISIONS,
    GENESIS_HASH,
    _compute_row_hash,
    _utc_timestamp,
)
from app.mongo_client import get_database

logger = logging.getLogger(__name__)

__all__ = [
    "audit_log_exists",
    "get_audit_logs",
    "initialize_database",
    "insert_audit_log",
    "verify_audit_chain",
]

_COLLECTION = "audit_logs"
_HEAD_ID = "audit_chain_head"
_SEQ_ID = "audit_seq"

_write_lock = threading.Lock()
_indexes_ready: set[int] = set()


def _as_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _ensure_indexes() -> None:
    """Create the indexes the audit log relies on (idempotent)."""
    database = get_database()
    key = id(database.client)
    if key in _indexes_ready:
        return
    database[_COLLECTION].create_index(
        [("thread_id", 1), ("metric_name", 1), ("decision", 1)],
        unique=True,
        name="idx_audit_logs_idempotency",
    )
    database[_COLLECTION].create_index([("seq", 1)], unique=True, name="idx_audit_logs_seq")
    database[_COLLECTION].create_index([("thread_id", 1)], name="idx_audit_logs_thread_id")
    _indexes_ready.add(key)


def initialize_database(db_path: str | None = None) -> str:
    """Ensure the MongoDB collections and indexes exist.

    Returns a short description of where the audit log now lives.
    """
    _ensure_indexes()
    database = get_database()
    return f"mongodb://{database.name}.{_COLLECTION}"


def _next_sequence(database: Any) -> int:
    """Atomically allocate the next sequence number."""
    from pymongo import ReturnDocument

    document = database["counters"].find_one_and_update(
        {"_id": _SEQ_ID},
        {"$inc": {"value": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(document["value"])


def _chain_head(database: Any) -> str:
    document = database["chain_head"].find_one({"_id": _HEAD_ID})
    if not document:
        return GENESIS_HASH
    return str(document.get("last_hash") or GENESIS_HASH)


def insert_audit_log(
    *,
    thread_id: str,
    company_name: str,
    cik: str,
    metric_name: str,
    original_value: float | None,
    corrected_value: float | None,
    decision: str,
    reason: str,
    reviewer_notes: str = "",
    timestamp: str | None = None,
    db_path: str | None = None,
) -> int:
    """Append one audit record and return its id.

    The unique ``(thread_id, metric_name, decision)`` index makes replays
    idempotent, exactly like the SQLite ``INSERT OR IGNORE``.
    """
    normalized_decision = decision.strip().upper()
    if normalized_decision not in AUDIT_DECISIONS:
        raise ValueError(
            f"Invalid audit decision {decision!r}; expected one of "
            f"{', '.join(AUDIT_DECISIONS)}."
        )

    database = get_database()
    _ensure_indexes()
    collection = database[_COLLECTION]

    row = {
        "thread_id": thread_id,
        "company_name": company_name,
        "cik": cik,
        "metric_name": metric_name,
        "original_value": _as_float(original_value),
        "corrected_value": _as_float(corrected_value),
        "decision": normalized_decision,
        "reason": reason,
        "reviewer_notes": reviewer_notes,
        "timestamp": timestamp or _utc_timestamp(),
    }

    with _write_lock:
        sequence = _next_sequence(database)
        prev_hash = _chain_head(database)
        row_hash = _compute_row_hash(row, prev_hash)
        document = {**row, "seq": sequence, "id": sequence, "prev_hash": prev_hash, "row_hash": row_hash}
        try:
            collection.insert_one(document)
        except Exception as error:  # noqa: BLE001 - only duplicates are tolerated
            if not _is_duplicate_key(error):
                raise
            existing = collection.find_one(
                {
                    "thread_id": thread_id,
                    "metric_name": metric_name,
                    "decision": normalized_decision,
                }
            )
            return int(existing["id"]) if existing else 0
        database["chain_head"].replace_one(
            {"_id": _HEAD_ID},
            {"_id": _HEAD_ID, "last_hash": row_hash, "seq": sequence},
            upsert=True,
        )
        return sequence


def _is_duplicate_key(error: Exception) -> bool:
    try:
        from pymongo.errors import DuplicateKeyError

        return isinstance(error, DuplicateKeyError)
    except Exception:  # noqa: BLE001 - pymongo should always be importable here
        return "duplicate key" in str(error).lower()


def get_audit_logs(
    thread_id: str | None = None,
    *,
    decision: str | None = None,
) -> list[dict[str, Any]]:
    """Return audit records in append order, optionally filtered."""
    normalized_decision = decision.strip().upper() if decision else None
    if normalized_decision is not None and normalized_decision not in AUDIT_DECISIONS:
        raise ValueError(
            f"Invalid audit decision {decision!r}; expected one of "
            f"{', '.join(AUDIT_DECISIONS)}."
        )

    database = get_database()
    _ensure_indexes()
    query: dict[str, Any] = {}
    if thread_id is not None:
        query["thread_id"] = thread_id
    if normalized_decision is not None:
        query["decision"] = normalized_decision

    cursor = database[_COLLECTION].find(query).sort("seq", 1)
    rows: list[dict[str, Any]] = []
    for document in cursor:
        row = dict(document)
        row.pop("_id", None)
        rows.append(row)
    return rows


def audit_log_exists(
    thread_id: str,
    metric_name: str,
    decision: str | None = None,
) -> bool:
    """Return whether a decision was already persisted for a thread/metric."""
    database = get_database()
    _ensure_indexes()
    query: dict[str, Any] = {"thread_id": thread_id, "metric_name": metric_name}
    if decision is not None:
        query["decision"] = decision.strip().upper()
    return database[_COLLECTION].find_one(query, {"_id": 1}) is not None


def verify_audit_chain(db_path: str | None = None) -> dict[str, Any]:
    """Verify every hashed audit row against its predecessor."""
    from app.database import verify_rows

    database = get_database()
    _ensure_indexes()
    rows: list[dict[str, Any]] = []
    for document in database[_COLLECTION].find({}).sort("seq", 1):
        row = dict(document)
        row.pop("_id", None)
        rows.append(row)
    return verify_rows(rows)
