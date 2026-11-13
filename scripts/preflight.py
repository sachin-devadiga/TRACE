#!/usr/bin/env python3
"""TRACE preflight checks.

Verifies that the live-demo environment is ready: SEC User-Agent, cached
filings for all five companies, LLM provider reachability, free ports, and a
clean database state. Also supports ``--reset`` for a one-command demo reset.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
CACHE_DIR = BACKEND / "data" / "sec_cache"

COMPANIES = {
    "AAPL": "0000320193",
    "MSFT": "0000789019",
    "NVDA": "0001045810",
    "TSLA": "0001318605",
    "AMZN": "0001018724",
}

EXPECTED_PORTS = []
FAILURES: list[str] = []
WARNINGS: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    if condition:
        print(f"  [OK]   {label}")
    else:
        print(f"  [FAIL] {label}{': ' + detail if detail else ''}")
        FAILURES.append(label)


def warn(condition: bool, label: str, detail: str = "") -> None:
    if condition:
        print(f"  [OK]   {label}")
    else:
        print(f"  [WARN] {label}{': ' + detail if detail else ''}")
        WARNINGS.append(label)


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def main() -> int:
    parser = argparse.ArgumentParser(description="TRACE preflight checks")
    parser.add_argument("--reset", action="store_true", help="Delete local demo databases and cache")
    args = parser.parse_args()

    print("TRACE preflight\n")

    print("1. SEC user agent")
    agent = (os.getenv("SEC_USER_AGENT") or "").strip()
    check(bool(agent), "SEC_USER_AGENT is set", "set SEC_USER_AGENT='Name email@example.com'")
    warn("@" in agent, "SEC_USER_AGENT looks like an email address")

    print("\n2. Offline cache")
    check(CACHE_DIR.exists(), f"cache directory exists ({CACHE_DIR})", "run a company audit while online")
    for ticker, cik in COMPANIES.items():
        cached = list(CACHE_DIR.glob(f"*{cik}*")) if CACHE_DIR.exists() else []
        check(bool(cached), f"{ticker} cache present ({cik})", "run that company online once")

    print("\n3. LLM provider")
    provider = (os.getenv("LLM_PROVIDER") or os.getenv("TRACE_LLM_PROVIDER") or "").strip().lower()
    if provider in {"ollama", "local"}:
        port_open = not port_free(11434)
        check(port_open, "Ollama reachable on 127.0.0.1:11434", "start `ollama serve`")
    else:
        has_key = any(os.getenv(k) for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"))
        warn(has_key, "an LLM API key is configured", "set GEMINI_API_KEY or GROQ_API_KEY")

    print("\n4. Ports")
    for port in (8000, 5173):
        check(port_free(port), f"port {port} is free", "stop the process using it")

    print("\n5. Databases")
    db_files = [BACKEND / "data" / "auditor.db", BACKEND / "data" / "checkpoints.db"]
    if args.reset:
        for db in db_files:
            for suffix in ("", "-wal", "-shm"):
                path = Path(str(db) + suffix)
                if path.exists():
                    path.unlink()
        if CACHE_DIR.exists():
            for item in CACHE_DIR.glob("*"):
                item.unlink()
        print("  [OK]   demo databases and cache reset")
    else:
        for db in db_files:
            check(not db.exists(), f"{db.name} absent (clean state)", "run with --reset for a clean demo")

    print("\nSummary")
    if FAILURES:
        print(f"  {len(FAILURES)} failure(s), {len(WARNINGS)} warning(s)")
        return 1
    print(f"  ready ({len(WARNINGS)} warning(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
