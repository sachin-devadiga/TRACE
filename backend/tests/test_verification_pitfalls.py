"""Gate 4: fiscal periods, XBRL duplicates, tag fallbacks, missing truth, units."""

from __future__ import annotations

from app import edgar_client
from app import graph as audit_graph
from app.schemas import ExtractedMetric
from app.units import normalize_value_to_usd, parse_unit


def _fact(
    value: float,
    *,
    start: str,
    end: str,
    accession: str,
    filed: str = "2025-11-01",
) -> dict:
    return {
        "start": start,
        "end": end,
        "val": value,
        "accn": accession,
        "fy": "FY2025",
        "fp": "FY",
        "form": "10-K",
        "filed": filed,
        "frame": f"CY{end[:4]}",
    }


def test_fiscal_year_is_matched_by_period_end_not_calendar_year():
    """Apple-style September year-end: FY2025 ends 2025-09-27, not 2025-12-31."""
    facts = {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {
            "units": {
                "USD": [
                    _fact(400_000, start="2024-09-29", end="2025-09-27", accession="A"),
                    _fact(380_000, start="2023-09-30", end="2024-09-28", accession="A"),
                    # A calendar-year fact from a different filing must not win.
                    _fact(999_999, start="2025-01-01", end="2025-12-31", accession="B"),
                ]
            }
        }
    }
    entries = edgar_client._annual_entries(
        facts,
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "A",
        duration=True,
    )
    assert [entry["end"] for entry in entries] == ["2025-09-27", "2024-09-28"]
    assert entries[0]["val"] == 400_000


def test_nvidia_january_year_end_is_accepted():
    facts = {
        "Revenues": {
            "units": {
                "USD": [
                    _fact(130_000, start="2025-01-27", end="2026-01-25", accession="NVDA-A"),
                ]
            }
        }
    }
    entries = edgar_client._annual_entries(
        facts, "Revenues", "NVDA-A", duration=True
    )
    assert entries[0]["end"] == "2026-01-25"


def test_duplicate_xbrl_fact_in_same_period_keeps_latest_filing():
    facts = {
        "Assets": {
            "units": {
                "USD": [
                    {"end": "2025-09-27", "val": 100, "accn": "A", "form": "10-K", "fp": "FY", "filed": "2025-11-01"},
                    {"end": "2025-09-27", "val": 101, "accn": "A", "form": "10-K", "fp": "FY", "filed": "2026-01-15"},
                ]
            }
        }
    }
    entries = edgar_client._annual_entries(facts, "Assets", "A", duration=False)
    assert len(entries) == 1
    assert entries[0]["val"] == 101, "latest-filed duplicate should win"


def test_xbrl_fact_from_another_filing_is_not_used():
    facts = {
        "Assets": {
            "units": {
                "USD": [
                    {"end": "2025-09-27", "val": 100, "accn": "ORIGINAL", "form": "10-K", "fp": "FY", "filed": "2025-11-01"},
                    {"end": "2025-09-27", "val": 777, "accn": "OTHER", "form": "10-K", "fp": "FY", "filed": "2025-11-02"},
                ]
            }
        }
    }
    entries = edgar_client._annual_entries(facts, "Assets", "ORIGINAL", duration=False)
    assert [entry["val"] for entry in entries] == [100]


def test_original_10k_is_preferred_over_10ka_amendment():
    submissions = {
        "filings": {
            "recent": {
                "form": ["10-K/A", "10-K"],
                "accessionNumber": ["0000-25-AMEND", "0000-25-ORIG"],
                "filingDate": ["2026-02-01", "2025-11-01"],
                "reportDate": ["2025-09-27", "2025-09-27"],
                "primaryDocument": ["amend.htm", "original.htm"],
            }
        }
    }
    filing = edgar_client._latest_10k_filing(submissions)
    assert filing["accession_number"] == "0000-25-ORIG"


def test_revenue_tag_falls_back_when_primary_tag_is_absent():
    facts = {
        "Revenues": {
            "units": {
                "USD": [
                    _fact(50_000, start="2024-01-01", end="2024-12-31", accession="A"),
                ]
            }
        }
    }
    tag, entries = edgar_client._pick_series(
        facts,
        edgar_client._GAAP_TAG_ALIASES["Revenue"],
        "A",
        duration=True,
    )
    assert tag == "Revenues"
    assert entries and entries[0]["val"] == 50_000


def test_missing_ground_truth_is_reported_as_no_ground_truth(monkeypatch):
    monkeypatch.setattr(audit_graph, "get_audit_logs", lambda *args, **kwargs: [])
    state = {
        "thread_id": "audit-missing",
        "company_name": "Amazon",
        "cik": "0001018724",
        "extracted_metrics": [
            {
                "metric_name": "Total Liabilities",
                "value": 123.0,
                "original_value": 123.0,
                "unit": "USD Millions",
                "value_usd": 123_000_000.0,
            }
        ],
        "original_metrics": [
            {
                "metric_name": "Total Liabilities",
                "value": 123.0,
                "original_value": 123.0,
                "unit": "USD Millions",
                "value_usd": 123_000_000.0,
            }
        ],
        "validation_checks": [],
        "flagged_items": [],
        "filing_metadata": {"fiscal_year": "FY2025"},
    }
    report = audit_graph._build_accuracy_report(
        state,
        {"metrics": {}, "fiscal_year": "FY2025", "period_end": "2025-12-31"},
    )
    detail = report["metric_details"][0]
    assert detail["xbrl_value"] is None
    assert detail["original_match"] is None
    assert "no ground truth" in detail["note"].lower()


def test_thousands_and_parentheses_are_normalized():
    assert parse_unit("$ in thousands").multiplier == 1_000
    assert normalize_value_to_usd(-1_234, "$ in thousands") == -1_234_000
    metric = ExtractedMetric(
        metric_name="Net Income",
        value="(1,234)",
        unit="USD thousands",
        period="FY2025",
        source_snippet="Net income (1,234)",
        confidence=0.9,
    )
    assert metric.value == -1_234
