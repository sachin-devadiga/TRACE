#!/usr/bin/env python3
"""Warm the EDGAR disk cache for all five demo companies.

Run once while online, then ``SEC_CACHE_ONLY=1`` (or simply disconnect the
network) makes the same audits replay from ``backend/data/sec_cache``.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.edgar_client import (  # noqa: E402
    TARGET_COMPANIES,
    fetch_latest_10k_tables,
    fetch_xbrl_ground_truth,
    sec_cache_dir,
)


def main() -> int:
    failures = 0
    for name, profile in TARGET_COMPANIES.items():
        cik = profile["cik"]
        try:
            filing = fetch_latest_10k_tables(cik)
            truth = fetch_xbrl_ground_truth(cik, filing.get("accession_number"))
            print(
                f"[OK]   {name} ({profile['ticker']}) {filing.get('accession_number')} "
                f"{len(filing.get('tables') or [])} tables, "
                f"{len(truth.get('metrics') or {})} XBRL metrics"
            )
        except Exception as error:  # noqa: BLE001 - report and continue the batch
            failures += 1
            print(f"[FAIL] {name} ({profile['ticker']}): {type(error).__name__}: {error}")

    cache_dir = sec_cache_dir()
    files = list(cache_dir.glob("*.json")) if cache_dir.exists() else []
    print(f"\nCache: {cache_dir} ({len(files)} response file(s))")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
