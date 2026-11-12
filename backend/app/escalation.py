"""Escalation typing shared by the validator, graph, API, and dashboard.

Every review flag belongs to one or both deterministic categories:

``EXTRACTION_RISK``
    The extracted value itself may be wrong: low confidence, failed snippet
    grounding, extractor disagreement, a failed accounting equation, a
    wrong-period column, or a failed cross-statement check.

``FINANCIAL_ANOMALY``
    The value is mechanically plausible but financially unusual: a large YoY
    movement or a segment-table anomaly.
"""

from __future__ import annotations

from typing import Any, Iterable

__all__ = [
    "ESCALATION_FINANCIAL_ANOMALY",
    "ESCALATION_EXTRACTION_RISK",
    "ESCALATION_TYPE_LABELS",
    "escalation_types_for_rules",
    "types_for_item",
]

ESCALATION_EXTRACTION_RISK = "EXTRACTION_RISK"
ESCALATION_FINANCIAL_ANOMALY = "FINANCIAL_ANOMALY"

ESCALATION_TYPE_LABELS = {
    ESCALATION_EXTRACTION_RISK: "Extraction risk",
    ESCALATION_FINANCIAL_ANOMALY: "Financial anomaly",
}

_EXTRACTION_RISK_RULES = {
    "Snippet Grounding",
    "Confidence Threshold",
    "Extractor Agreement",
    "Accounting Equation",
    "Column / Period",
    "Cross-Statement Check",
}
_FINANCIAL_ANOMALY_RULES = {
    "YoY Analysis",
    "Segment Validation",
    "Segment Table Check",
}


def escalation_types_for_rules(rules: Iterable[str] | None) -> list[str]:
    """Return the deterministic escalation categories for validation rules."""
    rule_names = {str(rule) for rule in (rules or [])}
    types: list[str] = []
    if rule_names & _EXTRACTION_RISK_RULES:
        types.append(ESCALATION_EXTRACTION_RISK)
    if rule_names & _FINANCIAL_ANOMALY_RULES:
        types.append(ESCALATION_FINANCIAL_ANOMALY)
    if not types:
        # Unknown or non-anomaly escalation: treat it as an extraction risk so
        # every review item always carries at least one badge.
        types.append(ESCALATION_EXTRACTION_RISK)
    return types


def types_for_item(item: dict[str, Any]) -> list[str]:
    """Return stored escalation types, deriving them for legacy checkpoints."""
    stored = item.get("escalation_types")
    if isinstance(stored, list) and stored:
        return [str(value) for value in stored]
    return escalation_types_for_rules(item.get("rules") or [])
