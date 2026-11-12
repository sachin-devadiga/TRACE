"""Storage facade: SQLite by default, MongoDB when ``MONGO_URI`` is set.

Every module imports the audit-log functions from here instead of from
:mod:`app.database` directly, so switching the backend is a configuration
change rather than a code change.

Resolution order:

1. ``MONGO_URI`` set  -> MongoDB (audit log and workflow checkpoints)
2. otherwise          -> the local SQLite files under ``backend/data/``
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from app import database as sqlite_store
from app.database import AUDIT_DECISIONS, GENESIS_HASH
from app.mongo_client import mongo_db_name, mongo_uri, using_mongodb

logger = logging.getLogger(__name__)

__all__ = [
    "AUDIT_DECISIONS",
    "GENESIS_HASH",
    "audit_log_exists",
    "backend_label",
    "clear_threads",
    "get_audit_logs",
    "get_checkpoint_path",
    "get_db_path",
    "initialize_database",
    "insert_audit_log",
    "list_thread_ids",
    "thread_exists",
    "using_mongodb",
    "verify_audit_chain",
]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CHECKPOINT_DB = _PROJECT_ROOT / "data" / "checkpoints.db"


def get_checkpoint_path() -> Path:
    """Return the SQLite checkpoint path (honours ``AUDITOR_CHECKPOINT_DB``)."""
    override = (os.getenv("AUDITOR_CHECKPOINT_DB") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return DEFAULT_CHECKPOINT_DB


def backend_label() -> str:
    """Return a human-readable description of the active storage backend."""
    if using_mongodb():
        return f"MongoDB ({mongo_db_name()})"
    return "SQLite"


def _mongo_store() -> Any:
    from app import mongo_database

    return mongo_database


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def initialize_database(db_path: str | Path | None = None) -> str | Path:
    """Prepare the audit log storage (tables, indexes or collections)."""
    if using_mongodb():
        return _mongo_store().initialize_database()
    return sqlite_store.initialize_database(db_path)


def insert_audit_log(**kwargs: Any) -> int:
    """Append one audit record and return its id."""
    if using_mongodb():
        return _mongo_store().insert_audit_log(**kwargs)
    return sqlite_store.insert_audit_log(**kwargs)


def get_audit_logs(
    thread_id: str | None = None,
    *,
    decision: str | None = None,
) -> list[dict[str, Any]]:
    """Return audit records in append order, optionally filtered."""
    if using_mongodb():
        return _mongo_store().get_audit_logs(thread_id, decision=decision)
    return sqlite_store.get_audit_logs(thread_id, decision=decision)


def audit_log_exists(
    thread_id: str,
    metric_name: str,
    decision: str | None = None,
) -> bool:
    """Return whether a decision was already persisted for a thread/metric."""
    if using_mongodb():
        return _mongo_store().audit_log_exists(thread_id, metric_name, decision)
    return sqlite_store.audit_log_exists(thread_id, metric_name, decision)


def verify_audit_chain(db_path: str | Path | None = None) -> dict[str, Any]:
    """Verify every hashed audit row against its predecessor."""
    if using_mongodb():
        return _mongo_store().verify_audit_chain()
    return sqlite_store.verify_audit_chain(db_path)


def get_db_path() -> Path:
    """Return the SQLite path (always available for diagnostics)."""
    return sqlite_store.get_db_path()


# ---------------------------------------------------------------------------
# Workflow checkpoints
# ---------------------------------------------------------------------------


def list_thread_ids() -> list[str]:
    """Return every known audit thread id, newest first."""
    if using_mongodb():
        from app.mongo_client import get_database

        database = get_database()
        rows = list(
            database["checkpoints"]
            .aggregate(
                [
                    {"$group": {"_id": "$thread_id", "last": {"$max": "$checkpoint_id"}}},
                    {"$sort": {"last": -1}},
                ]
            )
        )
        return [str(row["_id"]) for row in rows]

    path = sqlite_store.get_db_path()
    checkpoint_db = get_checkpoint_path()
    if not checkpoint_db.exists():
        return []
    connection = sqlite3.connect(str(checkpoint_db))
    try:
        try:
            rows = connection.execute(
                """
                SELECT thread_id, MAX(rowid) AS last_row
                FROM checkpoints
                GROUP BY thread_id
                ORDER BY last_row DESC
                """
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [row[0] for row in rows]
    finally:
        connection.close()


def thread_exists(thread_id: str) -> bool:
    """Return whether a thread has stored checkpoint state."""
    if using_mongodb():
        from app.mongo_client import get_database

        database = get_database()
        return (
            database["checkpoints"].find_one({"thread_id": thread_id}, {"_id": 1}) is not None
        )

    checkpoint_db = get_checkpoint_path()
    if not checkpoint_db.exists():
        return False
    connection = sqlite3.connect(str(checkpoint_db))
    try:
        try:
            row = connection.execute(
                "SELECT 1 FROM checkpoints WHERE thread_id = ? LIMIT 1",
                (thread_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return False
        return row is not None
    finally:
        connection.close()


def clear_threads(thread_ids: list[str]) -> int:
    """Delete the checkpoint state of the given threads.

    The append-only audit log is never touched.
    """
    if not thread_ids:
        return 0
    if using_mongodb():
        from app.mongo_client import get_database

        database = get_database()
        removed = 0
        for thread_id in thread_ids:
            result = database["checkpoints"].delete_many({"thread_id": thread_id})
            database["checkpoint_writes"].delete_many({"thread_id": thread_id})
            removed += int(result.deleted_count)
        return removed

    checkpoint_db = get_checkpoint_path()
    if not checkpoint_db.exists():
        return 0
    connection = sqlite3.connect(str(checkpoint_db), timeout=30.0)
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
