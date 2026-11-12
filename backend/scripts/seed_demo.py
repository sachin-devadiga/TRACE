"""Seed deterministic demo audit threads for the integrated TRACE backend.

Thin wrapper around :mod:`app.demo_seed` so the seeding logic lives in the
application package and can be reused by the ``POST /demo/reset`` endpoint.

Usage::

    python scripts/seed_demo.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.demo_seed import seed_demo_threads  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    auditor_db = os.getenv("AUDITOR_DB_PATH") or str(
        PROJECT_ROOT / "data" / "auditor.db"
    )
    checkpoint_db = os.getenv("AUDITOR_CHECKPOINT_DB") or str(
        PROJECT_ROOT / "data" / "checkpoints.db"
    )
    seeded = seed_demo_threads(checkpoint_db=checkpoint_db, auditor_db=auditor_db)
    print("Seeded audit threads:", ", ".join(seeded))


if __name__ == "__main__":
    main()
