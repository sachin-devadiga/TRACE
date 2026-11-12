"""MongoDB checkpoint store tests.

The LangGraph workflow state must live in MongoDB when ``MONGO_URI`` is set,
and the thread listing must find it there.  An in-process MongoDB mock keeps
the test hermetic.
"""

from __future__ import annotations

import pytest

mongomock = pytest.importorskip("mongomock")

from app import graph as graph_module  # noqa: E402
from app import mongo_client, store  # noqa: E402


@pytest.fixture()
def mongo(monkeypatch):
    client = mongomock.MongoClient()
    monkeypatch.setattr(mongo_client, "_client", client)
    monkeypatch.setenv("MONGO_URI", "mongodb://mock/trace")
    monkeypatch.setenv("MONGO_DB_NAME", "trace")
    yield client
    mongo_client.reset_client()


def test_checkpointer_uses_mongodb_when_uri_is_set(mongo) -> None:
    from langgraph.checkpoint.mongodb import MongoDBSaver

    connection, saver = graph_module.build_checkpointer()
    assert isinstance(saver, MongoDBSaver)
    # The connection handle is the saver itself, so shutdown closes the client.
    assert connection is saver


def test_workflow_state_round_trips_through_mongodb(mongo) -> None:
    _, saver = graph_module.build_checkpointer()
    graph = graph_module.build_audit_graph(checkpointer=saver)
    config = {"configurable": {"thread_id": "audit-mongo-graph"}}

    graph.update_state(config, {"thread_id": "audit-mongo-graph", "status": "STARTED"})
    snapshot = graph.get_state(config)
    assert (snapshot.values or {}).get("status") == "STARTED"

    # The state really is in MongoDB, not in a local SQLite file.
    assert mongo["trace"]["checkpoints"].count_documents({}) >= 1

    assert store.using_mongodb() is True
    assert "audit-mongo-graph" in store.list_thread_ids()
    assert store.thread_exists("audit-mongo-graph") is True
    assert store.thread_exists("audit-not-there") is False


def test_clear_threads_removes_mongodb_checkpoints(mongo) -> None:
    _, saver = graph_module.build_checkpointer()
    graph = graph_module.build_audit_graph(checkpointer=saver)
    config = {"configurable": {"thread_id": "audit-mongo-clear"}}
    graph.update_state(config, {"thread_id": "audit-mongo-clear", "status": "STARTED"})
    assert "audit-mongo-clear" in store.list_thread_ids()

    store.clear_threads(["audit-mongo-clear"])
    assert "audit-mongo-clear" not in store.list_thread_ids()
