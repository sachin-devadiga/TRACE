"""FastAPI application entry point for the combined TRACE + backend integration.

This module wires the production TRACE web-UI contract router (added on top of
the original backend app created in :mod:`app.main`) into a single FastAPI
application, replacing the Streamlit-era backend routes that are not part of
the UI contract.

Run from the project root with::

    uvicorn app.api:app --reload
"""

from __future__ import annotations

from app.main import app
from app.trace_contract import router as trace_router

# Backend routes that only serve the Streamlit-era workbench and are not part of
# the TRACE web-UI contract; the UI router owns the contract paths instead.
_BACKEND_ONLY_PATHS = {
    "/audit/start",
    "/audit/threads",
    "/audit/{thread_id}/status",
    "/audit/{thread_id}/timeline",
    "/audit/{thread_id}/resume",
    "/audit/logs",
    "/audit/verify",
    "/health",
    "/companies",
}

app.router.routes = [
    route
    for route in app.router.routes
    if getattr(route, "path", None) not in _BACKEND_ONLY_PATHS
]

app.include_router(trace_router)

__all__ = ["app"]
