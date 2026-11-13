"""Append-only SQLite audit log for the PS-01 filing auditor.

The audit trail lives in ``data/auditor.db`` by default.  Updates and deletes
are rejected by SQLite triggers, so an audit record can only be appended; any
attempt to rewrite history raises :class:`sqlite3.IntegrityError`.

Rows are idempotent per ``(thread_id, metric_name, decision)`` and carry a
SHA-256 hash chain (``prev_hash`` -> ``row_hash``) so tampering can be detected
by :func:`verify_audit_chain`.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

__all__ = [
    "AUDIT_DECISIONS",
    "DEFAULT_DB_PATH",
    "GENESIS_HASH",
    "audit_log_exists",
    "get_audit_logs",
    "get_db_path",
    "initialize_database",
    "insert_audit_log",
    "verify_audit_chain",
    "verify_rows",
]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "auditor.db"

AUDIT_DECISIONS = ("APPROVED", "CORRECTED", "REJECTED", "AUTO_APPROVED")
GENESIS_HASH = "GENESIS"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    company_name TEXT NOT NULL,
    cik TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    original_value REAL,
    corrected_value REAL,
    decision TEXT NOT NULL CHECK (
        decision IN ('APPROVED', 'CORRECTED', 'REJECTED', 'AUTO_APPROVED')
    ),
    reason TEXT NOT NULL,
    reviewer_notes TEXT,
    timestamp TEXT NOT NULL,
    prev_hash TEXT,
    row_hash TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_logs_idempotency
    ON audit_logs(thread_id, metric_name, decision);

CREATE INDEX IF NOT EXISTS idx_audit_logs_thread_id
    ON audit_logs(thread_id);

CREATE TRIGGER IF NOT EXISTS prevent_audit_update
BEFORE UPDATE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, 'Audit log is strictly append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_audit_delete
BEFORE DELETE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, 'Audit log is strictly append-only');
END;
"""


def get_db_path() -> Path:
    """Return the configured audit database path.

    ``AUDITOR_DB_PATH`` can override the default, which is useful for tests and
    for deployments that keep writable state outside the source tree.
    """
    override = (os.getenv("AUDITOR_DB_PATH") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return DEFAULT_DB_PATH


def _ensure_schema(connection: sqlite3.Connection) -> None:
    """Create or migrate the audit schema without deleting existing rows."""
    connection.executescript(_SCHEMA)
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(audit_logs)").fetchall()
    }
    for column in ("prev_hash", "row_hash"):
        if column not in columns:
            connection.execute(f"ALTER TABLE audit_logs ADD COLUMN {column} TEXT")
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_logs_idempotency "
        "ON audit_logs(thread_id, metric_name, decision)"
    )
    connection.commit()


def _connect(
    db_path: Path | str | None = None,
    *,
    ensure_schema: bool = False,
) -> sqlite3.Connection:
    path = Path(db_path).expanduser().resolve() if db_path else get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    if ensure_schema:
        _ensure_schema(connection)
    return connection


def initialize_database(db_path: Path | str | None = None) -> Path:
    """Create the audit table, hash columns, idempotency index, and triggers."""
    path = Path(db_path).expanduser().resolve() if db_path else get_db_path()
    connection = _connect(path, ensure_schema=True)
    connection.close()
    return path


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _as_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _hash_payload(row: Mapping[str, Any], prev_hash: str) -> dict[str, Any]:
    """Return the canonical field set covered by the row hash."""
    return {
        "thread_id": str(row.get("thread_id") or ""),
        "company_name": str(row.get("company_name") or ""),
        "cik": str(row.get("cik") or ""),
        "metric_name": str(row.get("metric_name") or ""),
        "original_value": _as_float(row.get("original_value")),
        "corrected_value": _as_float(row.get("corrected_value")),
        "decision": str(row.get("decision") or ""),
        "reason": str(row.get("reason") or ""),
        "reviewer_notes": str(row.get("reviewer_notes") or ""),
        "timestamp": str(row.get("timestamp") or ""),
        "prev_hash": prev_hash,
    }


def _compute_row_hash(row: Mapping[str, Any], prev_hash: str) -> str:
    encoded = json.dumps(
        _hash_payload(row, prev_hash),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _last_row_hash(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        "SELECT row_hash FROM audit_logs WHERE row_hash IS NOT NULL "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return str(row[0]) if row and row[0] else GENESIS_HASH


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
    db_path: Path | str | None = None,
) -> int:
    """Append one audit record and return its row id.

    ``INSERT OR IGNORE`` plus the unique ``(thread_id, metric_name, decision)``
    index makes node replays idempotent.  The database triggers still make the
    table append-only; this function never updates an existing row.
    """
    normalized_decision = decision.strip().upper()
    if normalized_decision not in AUDIT_DECISIONS:
        raise ValueError(
            f"Invalid audit decision {decision!r}; expected one of "
            f"{', '.join(AUDIT_DECISIONS)}."
        )

    path = Path(db_path).expanduser().resolve() if db_path else get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = _connect(path, ensure_schema=True)
    try:
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
        # Serialize the read of the previous hash and the append.  Without this
        # two parallel audits could read the same predecessor and fork the
        # hash chain even though SQLite serializes the writes themselves.
        connection.execute("BEGIN IMMEDIATE")
        prev_hash = _last_row_hash(connection)
        row_hash = _compute_row_hash(row, prev_hash)
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO audit_logs (
                thread_id, company_name, cik, metric_name,
                original_value, corrected_value, decision,
                reason, reviewer_notes, timestamp, prev_hash, row_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["thread_id"],
                row["company_name"],
                row["cik"],
                row["metric_name"],
                row["original_value"],
                row["corrected_value"],
                row["decision"],
                row["reason"],
                row["reviewer_notes"],
                row["timestamp"],
                prev_hash,
                row_hash,
            ),
        )
        connection.commit()
        if cursor.rowcount == 0:
            existing = connection.execute(
                """
                SELECT id FROM audit_logs
                WHERE thread_id = ? AND metric_name = ? AND decision = ?
                ORDER BY id ASC LIMIT 1
                """,
                (thread_id, metric_name, normalized_decision),
            ).fetchone()
            return int(existing[0]) if existing else 0
        return int(cursor.lastrowid)
    finally:
        connection.close()


def get_audit_logs(
    thread_id: str | None = None,
    *,
    decision: str | None = None,
) -> list[dict[str, Any]]:
    """Return audit records in append order, optionally filtered.

    Args:
        thread_id: Restrict to one audit thread.
        decision: Restrict to ``APPROVED``, ``CORRECTED``, ``REJECTED``, or
            ``AUTO_APPROVED``.
    """
    normalized_decision = decision.strip().upper() if decision else None
    if normalized_decision is not None and normalized_decision not in AUDIT_DECISIONS:
        raise ValueError(
            f"Invalid audit decision {decision!r}; expected one of "
            f"{', '.join(AUDIT_DECISIONS)}."
        )

    clauses: list[str] = []
    parameters: list[Any] = []
    if thread_id is not None:
        clauses.append("thread_id = ?")
        parameters.append(thread_id)
    if normalized_decision is not None:
        clauses.append("decision = ?")
        parameters.append(normalized_decision)

    query = "SELECT * FROM audit_logs"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id ASC"

    connection = _connect(ensure_schema=True)
    try:
        rows = connection.execute(query, tuple(parameters)).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def audit_log_exists(
    thread_id: str,
    metric_name: str,
    decision: str | None = None,
) -> bool:
    """Return whether a decision was already persisted for a thread/metric."""
    connection = _connect(ensure_schema=True)
    try:
        if decision is None:
            row = connection.execute(
                """
                SELECT 1 FROM audit_logs
                WHERE thread_id = ? AND metric_name = ?
                LIMIT 1
                """,
                (thread_id, metric_name),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT 1 FROM audit_logs
                WHERE thread_id = ? AND metric_name = ? AND decision = ?
                LIMIT 1
                """,
                (thread_id, metric_name, decision.strip().upper()),
            ).fetchone()
        return row is not None
    finally:
        connection.close()


def verify_rows(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Verify a sequence of hashed audit rows against their predecessor.

    Shared by the SQLite and the MongoDB stores so both produce identical
    results.  Rows written before hash support was added are reported as
    ``legacy_rows`` and skipped rather than treated as tampering.
    """
    last_hash = GENESIS_HASH
    checked_rows = 0
    legacy_rows = 0
    for row in rows:
        data = dict(row)
        stored_hash = data.get("row_hash")
        if not stored_hash:
            legacy_rows += 1
            continue

        prev_hash = str(data.get("prev_hash") or "")
        if prev_hash != last_hash:
            return {
                "verified": False,
                "message": f"Audit chain broken at row {data.get('id')}: prev_hash mismatch.",
                "total_rows": len(rows),
                "checked_rows": checked_rows,
                "legacy_rows": legacy_rows,
                "broken_row_id": data.get("id"),
                "broken_row": data,
            }

        expected = _compute_row_hash(data, prev_hash)
        if expected != stored_hash:
            return {
                "verified": False,
                "message": f"Audit chain broken at row {data.get('id')}: row_hash mismatch.",
                "total_rows": len(rows),
                "checked_rows": checked_rows,
                "legacy_rows": legacy_rows,
                "broken_row_id": data.get("id"),
                "broken_row": data,
            }

        last_hash = str(stored_hash)
        checked_rows += 1

    return {
        "verified": True,
        "message": "Audit chain intact.",
        "total_rows": len(rows),
        "checked_rows": checked_rows,
        "legacy_rows": legacy_rows,
        "broken_row_id": None,
        "broken_row": None,
    }


def verify_audit_chain(db_path: Path | str | None = None) -> dict[str, Any]:
    """Verify every hashed audit row against its predecessor.

    Returns a result dictionary with ``verified``, counts, and the first broken
    row (if any).  Rows written before hash support was added are reported as
    ``legacy_rows`` and skipped rather than treated as tampering.
    """
    connection = _connect(db_path, ensure_schema=True)
    try:
        rows = connection.execute("SELECT * FROM audit_logs ORDER BY id ASC").fetchall()
    finally:
        connection.close()

    return verify_rows([dict(row) for row in rows])
