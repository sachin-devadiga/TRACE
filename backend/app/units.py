"""Unit normalization for extracted 10-K values.

10-K tables are usually presented in thousands or millions, while SEC XBRL
Company Facts are reported in full USD.  Every metric therefore keeps both its
original value/unit and a normalized full-USD value used by the deterministic
validator and the XBRL accuracy comparison.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.schemas import ExtractedMetric

__all__ = [
    "UnitScale",
    "apply_unit_normalization",
    "normalize_metric_dict",
    "normalize_value_to_usd",
    "parse_unit",
]


@dataclass(frozen=True)
class UnitScale:
    """Parsed reporting unit and its multiplier to full USD."""

    multiplier: float
    normalized_unit: str
    scale_label: str
    recognized: bool


def parse_unit(unit: str | None) -> UnitScale:
    """Parse common 10-K unit labels into a multiplier for full USD.

    Examples:
        ``USD Millions`` -> 1_000_000
        ``$ in thousands`` -> 1_000
        ``USD`` -> 1
    """
    text = str(unit or "").strip().lower().replace("$", "usd")
    if re.search(r"\bbillions?\b", text):
        return UnitScale(1_000_000_000.0, "USD", "billions", True)
    if re.search(r"\bmillions?\b", text):
        return UnitScale(1_000_000.0, "USD", "millions", True)
    if re.search(r"\bthousands?\b", text):
        return UnitScale(1_000.0, "USD", "thousands", True)
    if "usd" in text or "dollar" in text or "units" in text:
        return UnitScale(1.0, "USD", "units", True)
    return UnitScale(1.0, "USD", "unknown", False)


def normalize_value_to_usd(value: float, unit: str | None) -> float:
    """Return ``value`` converted to full USD using the reported unit."""
    scale = parse_unit(unit)
    return round(float(value) * scale.multiplier, 6)


def normalize_metric_dict(metric: dict[str, Any]) -> dict[str, Any]:
    """Add normalized full-USD fields to a serialized metric in place.

    The original ``value`` and ``unit`` are never changed; ``original_value`` is
    captured once so later human corrections cannot erase the extraction.
    """
    if metric.get("original_value") is None:
        metric["original_value"] = metric.get("value")

    scale = parse_unit(metric.get("unit"))
    metric["unit_scale"] = scale.multiplier
    metric["normalized_unit"] = "USD"
    value = metric.get("value")
    prior = metric.get("prior_year_value")
    metric["value_usd"] = (
        round(float(value) * scale.multiplier, 6) if value is not None else None
    )
    metric["prior_year_value_usd"] = (
        round(float(prior) * scale.multiplier, 6) if prior is not None else None
    )
    return metric


def apply_unit_normalization(metric: "ExtractedMetric") -> "ExtractedMetric":
    """Populate normalized fields on an :class:`ExtractedMetric` instance."""
    if metric.original_value is None:
        metric.original_value = metric.value

    scale = parse_unit(metric.unit)
    metric.unit_scale = scale.multiplier
    metric.normalized_unit = "USD"
    metric.value_usd = round(float(metric.value) * scale.multiplier, 6)
    metric.prior_year_value_usd = (
        round(float(metric.prior_year_value) * scale.multiplier, 6)
        if metric.prior_year_value is not None
        else None
    )
    return metric
