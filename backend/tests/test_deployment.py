"""Deployment configuration tests.

The hosted setup (Render) injects origins and secrets through environment
variables, so the backend must read them instead of hard-coding them.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import main as app_main


def test_cors_origins_default_to_local_development(monkeypatch) -> None:
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert app_main._cors_origins() == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


def test_cors_origins_are_read_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv(
        "CORS_ORIGINS",
        "https://trace-frontend.onrender.com, http://localhost:5173 ,",
    )
    assert app_main._cors_origins() == [
        "https://trace-frontend.onrender.com",
        "http://localhost:5173",
    ]


def test_cors_preflight_allows_the_local_development_origin(
    tmp_path, monkeypatch
) -> None:
    """The middleware is built at import time from ``_cors_origins()``."""
    monkeypatch.setenv("AUDITOR_DB_PATH", str(tmp_path / "auditor.db"))
    monkeypatch.setenv("AUDITOR_CHECKPOINT_DB", str(tmp_path / "checkpoints.db"))
    monkeypatch.setattr(app_main, "_GRAPH", None)
    monkeypatch.setattr(app_main, "_CHECKPOINTER_CONNECTION", None)
    app_main.initialize_database()

    client = TestClient(app_main.app)
    response = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == (
        "http://localhost:5173"
    )

    app_main._GRAPH = None
    app_main._CHECKPOINTER_CONNECTION = None


def test_health_endpoint_is_used_as_the_render_health_check(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUDITOR_DB_PATH", str(tmp_path / "auditor.db"))
    monkeypatch.setenv("AUDITOR_CHECKPOINT_DB", str(tmp_path / "checkpoints.db"))
    monkeypatch.setattr(app_main, "_GRAPH", None)
    monkeypatch.setattr(app_main, "_CHECKPOINTER_CONNECTION", None)
    app_main.initialize_database()

    client = TestClient(app_main.app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    app_main._GRAPH = None
    app_main._CHECKPOINTER_CONNECTION = None
