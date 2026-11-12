"""Shared MongoDB connection for TRACE.

MongoDB is optional. When ``MONGO_URI`` is set the audit log and the workflow
checkpoints are stored in MongoDB; otherwise the application falls back to the
local SQLite files. The switch is read from the environment on every call, so
the deployment can be re-pointed by setting an environment variable alone.

Environment variables:

``MONGO_URI``
    Connection string, e.g. ``mongodb+srv://user:pass@cluster.mongodb.net``.
``MONGO_DB_NAME``
    Database name (default ``trace``).
``MONGO_SERVER_SELECTION_TIMEOUT_MS``
    How long to wait for a server (default 8000).
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "get_database",
    "get_client",
    "mongo_db_name",
    "mongo_uri",
    "reset_client",
    "using_mongodb",
]

_client: Any = None
_lock = threading.Lock()


def mongo_uri() -> str | None:
    """Return the configured MongoDB URI, or ``None`` when not configured."""
    uri = (os.getenv("MONGO_URI") or "").strip()
    return uri or None


def mongo_db_name() -> str:
    """Return the database name (default ``trace``)."""
    return (os.getenv("MONGO_DB_NAME") or "trace").strip() or "trace"


def using_mongodb() -> bool:
    """Return whether MongoDB is configured for this process."""
    return mongo_uri() is not None


def _server_selection_timeout_ms() -> int:
    raw = (os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS") or "").strip()
    try:
        return int(raw) if raw else 8000
    except ValueError:
        return 8000


def get_client() -> Any:
    """Return the process-wide Mongo client, creating it on first use."""
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                uri = mongo_uri()
                if not uri:
                    raise RuntimeError(
                        "MONGO_URI is not set, so MongoDB cannot be used."
                    )
                from pymongo import MongoClient

                _client = MongoClient(
                    uri,
                    serverSelectionTimeoutMS=_server_selection_timeout_ms(),
                    connectTimeoutMS=_server_selection_timeout_ms(),
                )
                logger.info("Connected to MongoDB database %s", mongo_db_name())
    return _client


def get_database() -> Any:
    """Return the MongoDB database handle."""
    return get_client()[mongo_db_name()]


def reset_client() -> None:
    """Close and forget the client (used by tests)."""
    global _client
    with _lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:  # noqa: BLE001 - closing must never raise
                pass
        _client = None
