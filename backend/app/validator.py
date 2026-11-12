"""Deterministic financial validation for extracted 10-K metrics.

This module intentionally contains no LLM calls. Every decision is a pure
function of the supplied :class:`ExtractedMetric` objects.  Values are
normalized to full USD first (via :mod:`app.units`), and each check stores an
explicit PASSED / FAILED / SKIPPED status plus its supporting details.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any, Callable, Iterable

from app.confidence import compute_confidence
from app.escalation import escalation_types_for_rules
from app.evidence import attach_cross_statement, attach_evidence_context
from app.schemas import ExtractedMetric, ValidationCheck
from app.units import apply_unit_normalization

__all__ = ["validate_metrics"]

SEVERITY_RANK = {"SKIPPED": 0, "PASS": 1, "WARNING": 2, "FAIL": 3}


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _value_digits(value: float) -> str:
    """Return the significant digits of a value, without separators/signs."""
    if float(value).is_integer():
        text = str(int(value))
    else:
        text = f"{value:g}"
    return re.sub(r"\D", "", text)


def _format_number(value: float) -> str:
    text = f"{value:,.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _usd_value(metric: ExtractedMetric) -> float:
    """Return the metric value in full USD, normalizing on demand."""
    if metric.value_usd is None:
        apply_unit_normalization(metric)
    return float(metric.value_usd or 0.0)


def _find_metric(
    metrics: Iterable[ExtractedMetric],
    predicate: Callable[[str], bool],
) -> ExtractedMetric | None:
    matches = [metric for metric in metrics if predicate(_normalize_name(metric.metric_name))]
    if not matches:
        return None
    return max(matches, key=lambda metric: metric.confidence_score or metric.confidence)


def _is_assets(name: str) -> bool:
    return name == "assets" or "total assets" in name


def _is_liabilities(name: str) -> bool:
    return name == "liabilities" or "total liabilities" in name


def _is_equity(name: str) -> bool:
    return "shareholders equity" in name or "stockholders equity" in name or name == "total equity"


def _add_flag(
    checks: list[ValidationCheck],
    flagged: "OrderedDict[str, dict[str, Any]]",
    metric: ExtractedMetric,
    rule_name: str,
    severity: str,
    reason: str,
    *,
    details: dict[str, Any] | None = None,
    metrics_involved: list[str] | None = None,
) -> None:
    passed = severity == "PASS"
    checks.append(
        ValidationCheck(
            rule_name=rule_name,
            metric_name=metric.metric_name,
            passed=passed,
            severity=severity,  # type: ignore[arg-type]
            reason=reason,
            details=details or {},
            metrics_involved=metrics_involved or [metric.metric_name],
        )
    )
    if severity in {"PASS", "SKIPPED"}:
        return

    key = _normalize_name(metric.metric_name)
    entry = flagged.setdefault(
        key,
        {
            "metric_name": metric.metric_name,
            "value": metric.value,
            "original_value": metric.original_value,
            "unit": metric.unit,
            "value_usd": metric.value_usd,
            "period": metric.period,
            "severity": severity,
            "rules": [],
            "reasons": [],
            "confidence": metric.confidence_score,
            "llm_confidence": metric.confidence,
            "confidence_label": metric.confidence_label,
            "confidence_signals": metric.confidence_signals,
            "extractor_agreement": metric.extractor_agreement,
            "extractor_agreement_details": metric.extractor_agreement_details,
            "source_snippet": metric.source_snippet,
            "source_table_index": metric.source_table_index,
            "source_table_title": metric.source_table_title,
            "source_table_category": metric.source_table_category,
            "source_header": metric.source_header,
            "source_row": metric.source_row,
            "source_row_text": metric.source_row_text,
            "value_column_index": metric.value_column_index,
            "value_column_header": metric.value_column_header,
            "period_column_matched": metric.period_column_matched,
            "cross_statement_match": metric.cross_statement_match,
            "cross_statement_note": metric.cross_statement_note,
            "details": {},
            "escalation_types": [],
        },
    )
    if SEVERITY_RANK[severity] > SEVERITY_RANK[entry["severity"]]:
        entry["severity"] = severity
    if rule_name not in entry["rules"]:
        entry["rules"].append(rule_name)
    entry["reasons"].append(reason)
    entry["escalation_types"] = escalation_types_for_rules(entry["rules"])
    if details:
        entry["details"][rule_name] = details


def validate_metrics(
    metrics: list[ExtractedMetric],
    confidence_threshold: float = 0.85,
    yoy_threshold_pct: float = 30.0,
    filing_metadata: dict[str, Any] | None = None,
    tables: list[dict[str, Any]] | None = None,
) -> tuple[list[ValidationCheck], list[dict[str, Any]]]:
    """Run all deterministic checks and return checks plus review items.

    The snippet-grounding rule mutates ``metric.confidence`` down by 0.25 when
    the value's digits are absent from its source snippet.  The deterministic
    confidence score is computed afterwards so it sees that penalty.
    """
    if not 0.0 <= confidence_threshold <= 1.0:
        raise ValueError("confidence_threshold must be between 0.0 and 1.0")
    if yoy_threshold_pct < 0:
        raise ValueError("yoy_threshold_pct must be non-negative")

    metadata = filing_metadata or {}
    checks: list[ValidationCheck] = []
    flagged: "OrderedDict[str, dict[str, Any]]" = OrderedDict()

    for metric in metrics:
        apply_unit_normalization(metric)

    # Reconstruct the source-table context before scoring.  Older checkpoints
    # may not have persisted evidence, so this is intentionally idempotent.
    attach_evidence_context(metrics, tables, metadata)
    attach_cross_statement(metrics, tables, metadata)

    # 1. Snippet grounding -------------------------------------------------
    for metric in metrics:
        value_digits = _value_digits(metric.value)
        snippet_digits = re.sub(r"\D", "", metric.source_snippet)
        grounded = bool(value_digits) and value_digits in snippet_digits

        if grounded:
            _add_flag(
                checks,
                flagged,
                metric,
                "Snippet Grounding",
                "PASS",
                f"Value digits {value_digits} appear in the source snippet.",
                details={"grounded": True, "value_digits": value_digits},
            )
        else:
            old_confidence = metric.confidence
            metric.confidence = round(max(0.0, old_confidence - 0.25), 4)
            _add_flag(
                checks,
                flagged,
                metric,
                "Snippet Grounding",
                "WARNING",
                (
                    f"Value digits {value_digits or '<none>'} do not appear in the source "
                    f"snippet; LLM confidence reduced from {old_confidence:.2f} to "
                    f"{metric.confidence:.2f}."
                ),
                details={
                    "grounded": False,
                    "value_digits": value_digits,
                    "llm_confidence_before": old_confidence,
                    "llm_confidence_after": metric.confidence,
                },
            )

    # 2. Extractor agreement ----------------------------------------------
    for metric in metrics:
        status = metric.extractor_agreement or "fallback-unavailable"
        agreement_details = dict(metric.extractor_agreement_details or {})
        if status == "disagree":
            difference = agreement_details.get("difference_pct")
            _add_flag(
                checks,
                flagged,
                metric,
                "Extractor Agreement",
                "WARNING",
                (
                    "Primary and fallback extractors disagree on the normalized "
                    f"USD value ({difference if difference is not None else 'unknown'}% "
                    "difference; tolerance 0.5%)."
                ),
                details={
                    **agreement_details,
                    "status": status,
                },
            )
        elif status == "agree":
            checks.append(
                ValidationCheck(
                    rule_name="Extractor Agreement",
                    metric_name=metric.metric_name,
                    passed=True,
                    severity="PASS",
                    reason=(
                        "Primary and fallback extractors agree within 0.5% after "
                        "full-USD normalization."
                    ),
                    details={**agreement_details, "status": status},
                )
            )
        else:
            checks.append(
                ValidationCheck(
                    rule_name="Extractor Agreement",
                    metric_name=metric.metric_name,
                    passed=False,
                    severity="SKIPPED",
                    reason=(
                        "The independent fallback comparison was unavailable; "
                        "extractor agreement could not be measured."
                    ),
                    details={**agreement_details, "status": status},
                )
            )

    # 3. Current-period column and cross-statement checks ------------------
    for metric in metrics:
        if metric.period_column_matched is not None:
            details = {
                "source_table": metric.source_table_title,
                "source_header": metric.source_header,
                "value_column_index": metric.value_column_index,
                "value_column_header": metric.value_column_header,
                "expected_period": metric.period,
                "matched": metric.period_column_matched,
            }
            if metric.period_column_matched:
                checks.append(
                    ValidationCheck(
                        rule_name="Column / Period",
                        metric_name=metric.metric_name,
                        passed=True,
                        severity="PASS",
                        reason=(
                            f"Value {_format_number(metric.value)} appears in the "
                            f"current-period column {metric.value_column_header!r}."
                        ),
                        details=details,
                    )
                )
            else:
                _add_flag(
                    checks,
                    flagged,
                    metric,
                    "Column / Period",
                    "WARNING",
                    (
                        "The extracted value appears in a column that does not match "
                        f"the filing period {metric.period!r} "
                        f"(column header {metric.value_column_header!r})."
                    ),
                    details=details,
                )
        if metric.cross_statement_match is not None:
            cross_details = {
                "matched": metric.cross_statement_match,
                "note": metric.cross_statement_note,
            }
            if metric.cross_statement_match:
                checks.append(
                    ValidationCheck(
                        rule_name="Cross-Statement Check",
                        metric_name=metric.metric_name,
                        passed=True,
                        severity="PASS",
                        reason=(
                            "Net Income agrees with the opening line of the "
                            "cash-flow statement within 0.5%."
                        ),
                        details=cross_details,
                    )
                )
            else:
                _add_flag(
                    checks,
                    flagged,
                    metric,
                    "Cross-Statement Check",
                    "WARNING",
                    (
                        "Net Income does not agree with the opening line of the "
                        "cash-flow statement within 0.5%."
                    ),
                    details=cross_details,
                )

    # 4. Deterministic confidence score (3.3) -----------------------------
    for metric in metrics:
        result = compute_confidence(
            metric,
            filing_metadata=metadata,
            threshold=confidence_threshold,
        )
        score = float(result["score"])
        label = str(result["label"])
        if score < confidence_threshold:
            _add_flag(
                checks,
                flagged,
                metric,
                "Confidence Threshold",
                "WARNING",
                (
                    f"Deterministic confidence score {score:.2f} ({label}) is below the "
                    f"{confidence_threshold:.2f} threshold; LLM self-confidence is "
                    f"{metric.confidence:.2f}."
                ),
                details={
                    "confidence_score": score,
                    "confidence_label": label,
                    "llm_confidence": metric.confidence,
                    "threshold": confidence_threshold,
                    "signals": metric.confidence_signals,
                },
            )
        else:
            _add_flag(
                checks,
                flagged,
                metric,
                "Confidence Threshold",
                "PASS",
                (
                    f"Deterministic confidence score {score:.2f} ({label}) meets the "
                    f"{confidence_threshold:.2f} threshold."
                ),
                details={
                    "confidence_score": score,
                    "confidence_label": label,
                    "llm_confidence": metric.confidence,
                    "threshold": confidence_threshold,
                    "signals": metric.confidence_signals,
                },
            )

    # 3. Accounting equation (full USD) -----------------------------------
    assets = _find_metric(metrics, _is_assets)
    liabilities = _find_metric(metrics, _is_liabilities)
    equity = _find_metric(metrics, _is_equity)

    if assets and liabilities and equity:
        assets_usd = _usd_value(assets)
        liabilities_usd = _usd_value(liabilities)
        equity_usd = _usd_value(equity)
        difference = assets_usd - (liabilities_usd + equity_usd)
        tolerance = max(abs(assets_usd) * 0.01, 1.0)
        equation_ok = abs(difference) <= tolerance
        reason = (
            f"Assets {_format_number(assets_usd)} USD vs liabilities + equity "
            f"{_format_number(liabilities_usd + equity_usd)} USD; difference "
            f"{_format_number(difference)} USD (tolerance {_format_number(tolerance)} USD)."
        )
        details = {
            "assets_usd": assets_usd,
            "liabilities_usd": liabilities_usd,
            "equity_usd": equity_usd,
            "difference_usd": difference,
            "tolerance_usd": tolerance,
            "equation": "Assets = Liabilities + Equity",
        }
        if equation_ok:
            checks.append(
                ValidationCheck(
                    rule_name="Accounting Equation",
                    metric_name="Total Assets = Total Liabilities + Shareholders' Equity",
                    passed=True,
                    severity="PASS",
                    reason=reason,
                    details=details,
                    metrics_involved=[
                        assets.metric_name,
                        liabilities.metric_name,
                        equity.metric_name,
                    ],
                )
            )
        else:
            for metric in (assets, liabilities, equity):
                _add_flag(
                    checks,
                    flagged,
                    metric,
                    "Accounting Equation",
                    "FAIL",
                    reason,
                    details=details,
                    metrics_involved=[
                        assets.metric_name,
                        liabilities.metric_name,
                        equity.metric_name,
                    ],
                )
    else:
        missing = [
            label
            for label, metric in (
                ("Total Assets", assets),
                ("Total Liabilities", liabilities),
                ("Shareholders' Equity", equity),
            )
            if metric is None
        ]
        reason = "Cannot evaluate the accounting equation; missing: " + ", ".join(missing)
        checks.append(
            ValidationCheck(
                rule_name="Accounting Equation",
                metric_name="Balance Sheet",
                passed=False,
                severity="WARNING",
                reason=reason,
                details={"missing_metrics": missing},
            )
        )
        flagged.setdefault(
            "balance sheet",
            {
                "metric_name": "Balance Sheet",
                "value": None,
                "unit": None,
                "period": None,
                "severity": "WARNING",
                "rules": ["Accounting Equation"],
                "reasons": [reason],
                "confidence": None,
                "llm_confidence": None,
                "confidence_label": None,
                "confidence_signals": {},
                "source_snippet": None,
                "details": {"Accounting Equation": {"missing_metrics": missing}},
                "escalation_types": ["EXTRACTION_RISK"],
            },
        )

    # 4. Year-on-year analysis (threshold configurable per run) -----------
    for metric in metrics:
        prior = metric.prior_year_value
        details = {
            "current_value": metric.value,
            "prior_year_value": prior,
            "unit": metric.unit,
            "threshold_pct": yoy_threshold_pct,
        }
        if prior is None:
            checks.append(
                ValidationCheck(
                    rule_name="YoY Analysis",
                    metric_name=metric.metric_name,
                    passed=False,
                    severity="SKIPPED",
                    reason=(
                        "No prior-year value was extracted; YoY variance cannot be "
                        f"evaluated at the +/-{yoy_threshold_pct:.2f}% threshold."
                    ),
                    details=details,
                )
            )
            continue
        if prior == 0:
            checks.append(
                ValidationCheck(
                    rule_name="YoY Analysis",
                    metric_name=metric.metric_name,
                    passed=False,
                    severity="SKIPPED",
                    reason=(
                        "Prior-year value is zero, so percentage variance is undefined; "
                        "the YoY check was skipped."
                    ),
                    details=details,
                )
            )
            continue

        pct_change = (metric.value - prior) / abs(prior) * 100.0
        exceeds = abs(pct_change) > yoy_threshold_pct
        details["pct_change"] = round(pct_change, 4)
        reason = (
            f"{_format_number(metric.value)} vs prior {_format_number(prior)} is "
            f"{pct_change:+.2f}% YoY (threshold +/-{yoy_threshold_pct:.2f}%)."
        )
        _add_flag(
            checks,
            flagged,
            metric,
            "YoY Analysis",
            "WARNING" if exceeds else "PASS",
            reason,
            details=details,
        )

    return checks, list(flagged.values())
