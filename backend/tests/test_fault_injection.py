"""Gate 2: injected extraction faults are caught for several metrics."""

from __future__ import annotations

import pytest

from app.schemas import ExtractedMetric
from app.validator import validate_metrics

METRIC_VALUES = {
    "Revenue": 1111,
    "Net Income": 2222,
    "Total Assets": 3333,
    "Total Liabilities": 4444,
    "Shareholders' Equity": 5555,
    "Operating Cash Flow": 6666,
    "Cash and Cash Equivalents": 7777,
    "Research and Development Expense": 8888,
}


def _metrics() -> list[ExtractedMetric]:
    return [
        ExtractedMetric(
            metric_name=name,
            value=float(value),
            prior_year_value=float(value) * 0.9,
            unit="USD Millions",
            period="FY2025",
            source_snippet=f"{name} $ {value:,} 12 months ended Sep 27, 2025",
            confidence=0.95,
            extractor_agreement="agree",
            source_header=["Metric", "FY2025"],
            source_row=[name, f"{value:,}"],
            source_row_text=f"{name} $ {value:,}",
        )
        for name, value in METRIC_VALUES.items()
    ]


@pytest.mark.parametrize("target", list(METRIC_VALUES))
def test_each_injected_metric_is_caught(target):
    metrics = _metrics()
    chosen = next(metric for metric in metrics if metric.metric_name == target)
    chosen.value = chosen.value * 1.1  # injected extraction error
    chosen.original_value = chosen.value  # keep history explicit

    _checks, flagged = validate_metrics(
        metrics,
        confidence_threshold=0.85,
        yoy_threshold_pct=30.0,
        filing_metadata={"report_date": "2025-09-27", "fiscal_year": "FY2025"},
        tables=[],
    )

    entries = {entry["metric_name"]: entry for entry in flagged}
    assert target in entries, f"{target} fault was not caught; flagged={list(entries)}"
    entry = entries[target]
    assert "Snippet Grounding" in entry["rules"], entry["rules"]


def test_multiple_injected_metrics_are_all_caught():
    metrics = _metrics()
    injected = {
        "Revenue": 999999,
        "Net Income": 888888,
        "Total Assets": 777777,
        "Operating Cash Flow": 666666,
    }
    for metric in metrics:
        if metric.metric_name in injected:
            metric.value = float(injected[metric.metric_name])

    _checks, flagged = validate_metrics(
        metrics,
        confidence_threshold=0.85,
        yoy_threshold_pct=30.0,
        filing_metadata={"report_date": "2025-09-27", "fiscal_year": "FY2025"},
        tables=[],
    )

    entries = {entry["metric_name"] for entry in flagged}
    for name in injected:
        assert name in entries, f"{name} fault was not caught; flagged={sorted(entries)}"
