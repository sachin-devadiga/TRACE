"""Gate 1.1 / Gate 5: EDGAR responses are cached and replay offline."""

from __future__ import annotations

import json

import pytest
import requests

from app import edgar_client


class _FakeResponse:
    def __init__(self, payload: dict, *, url: str = "https://data.sec.gov/test") -> None:
        self.url = url
        self.status_code = 200
        self.headers = {"content-type": "application/json"}
        self.content = json.dumps(payload).encode("utf-8")

    def json(self):
        return json.loads(self.content.decode("utf-8"))

    def raise_for_status(self) -> None:
        return None


@pytest.fixture()
def cache_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_CACHE_DIR", str(tmp_path / "sec_cache"))
    monkeypatch.delenv("SEC_CACHE_ONLY", raising=False)
    monkeypatch.delenv("TRACE_OFFLINE", raising=False)
    monkeypatch.setattr(edgar_client, "SEC_REQUEST_DELAY", 0.0)
    monkeypatch.setattr(edgar_client, "SEC_MAX_ATTEMPTS", 1)
    edgar_client._submissions.cache_clear()
    edgar_client._company_facts.cache_clear()
    return tmp_path / "sec_cache"


def test_online_fetch_writes_cache(cache_env, monkeypatch):
    payload = {"filings": {"recent": {"form": [], "accessionNumber": []}}, "name": "Test Co"}
    monkeypatch.setattr(edgar_client._SESSION, "get", lambda *a, **k: _FakeResponse(payload))

    result = edgar_client._submissions("0000000001")

    assert result["name"] == "Test Co"
    files = list(cache_env.glob("*.json"))
    assert len(files) == 1
    assert "0000000001" in files[0].name


def test_offline_replay_uses_cache_without_network(cache_env, monkeypatch):
    payload = {"filings": {"recent": {"form": [], "accessionNumber": []}}, "name": "Cached Co"}
    monkeypatch.setattr(edgar_client._SESSION, "get", lambda *a, **k: _FakeResponse(payload))
    edgar_client._submissions("0000000002")
    edgar_client._submissions.cache_clear()

    def _no_network(*args, **kwargs):
        raise AssertionError("network must not be touched in offline mode")

    monkeypatch.setenv("SEC_CACHE_ONLY", "1")
    monkeypatch.setattr(edgar_client._SESSION, "get", _no_network)

    result = edgar_client._submissions("0000000002")
    assert result["name"] == "Cached Co"


def test_network_failure_falls_back_to_cache(cache_env, monkeypatch):
    payload = {"filings": {"recent": {"form": [], "accessionNumber": []}}, "name": "Fallback Co"}
    monkeypatch.setattr(edgar_client._SESSION, "get", lambda *a, **k: _FakeResponse(payload))
    edgar_client._submissions("0000000003")
    edgar_client._submissions.cache_clear()

    def _offline(*args, **kwargs):
        raise requests.ConnectionError("network is down")

    monkeypatch.setattr(edgar_client._SESSION, "get", _offline)
    result = edgar_client._submissions("0000000003")
    assert result["name"] == "Fallback Co"


def test_offline_without_cache_raises_clear_error(cache_env, monkeypatch):
    monkeypatch.setenv("SEC_CACHE_ONLY", "1")
    with pytest.raises(RuntimeError, match="no cached SEC response"):
        edgar_client._submissions("0000000004")
