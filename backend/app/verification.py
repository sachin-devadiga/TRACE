"""Reusable financial data verification engine for TRACE.

This module provides source-based verification against SEC filings (XBRL)
without hardcoding company-specific values. It separates extraction confidence
from actual source verification and enforces balance sheet checks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app import edgar_client

logger = logging.getLogger(__name__)

# Required metrics for validation
REQUIRED_METRICS = {
    "Revenue",
    "Net Income",
    "Total Assets",
    "Total Liabilities",
    "Shareholders' Equity",
    "Operating Cash Flow",
    "Cash and Cash Equivalents",
}

BALANCE_SHEET_METRICS = ("Total Assets", "Total Liabilities", "Shareholders' Equity")


@dataclass
class VerificationResult:
    """Result of verifying a metric against source data."""

    metric_name: str
    role: str | None
    extracted_value: float | None
    extracted_unit: str | None
    source_value: float | None
    source_unit: str
    source_value_usd: float | None
    extracted_value_usd: float | None
    match: bool
    verified: bool  # True only if verified against actual source
    source_verified: bool  # Explicit flag - never True based on confidence alone
    extraction_confidence: float | None  # Separate from verification
    source_reference: dict[str, Any]
    filing_date: str | None
    period_end: str | None
    page_reference: str | None
    statement_reference: str | None
    tolerance_usd: float
    difference_usd: float | None
    currency: str = "USD"
    units_normalized: str = "USD Millions"
    issues: list[str] = field(default_factory=list)


@dataclass
class BalanceSheetValidation:
    """Balance sheet equation validation."""

    valid: bool
    total_assets: float | None
    total_liabilities: float | None
    total_equity: float | None
    calculated_assets: float | None
    difference: float | None
    tolerance: float
    issues: list[str] = field(default_factory=list)


@dataclass
class AuditVerificationReport:
    """Complete verification report for a filing."""

    company_name: str
    cik: str
    fiscal_year: str
    period_end: str
    filing_date: str
    accession_number: str
    source: str
    filing_url: str | None
    metrics: list[VerificationResult]
    balance_sheet_validation: BalanceSheetValidation | None
    overall_accuracy: float  # 0-100, never > 100
    verified_count: int
    total_count: int
    issues: list[str] = field(default_factory=list)


class FinancialVerificationEngine:
    """Reusable engine for verifying financial data against SEC sources."""

    DEFAULT_TOLERANCE_PCT = 0.1  # 0.1% tolerance for rounding
    MIN_TOLERANCE_USD = 1.0  # Minimum $1 tolerance

    def __init__(self, tolerance_pct: float = DEFAULT_TOLERANCE_PCT):
        self.tolerance_pct = tolerance_pct

    def fetch_source_truth(
        self, cik: str | int, target_fiscal_year: str | int | None = None
    ) -> dict[str, Any]:
        """Fetch official source data from SEC EDGAR (XBRL)."""
        truth = edgar_client.fetch_xbrl_ground_truth(cik)
        
        # If specific fiscal year requested, validate it
        if target_fiscal_year is not None:
            target_year = self._extract_year(target_fiscal_year)
            source_year = self._extract_year(truth.get("fiscal_year"))
            if target_year and source_year and target_year != source_year:
                raise ValueError(
                    f"Requested FY{target_year} but source is FY{source_year}. "
                    f"Use the correct filing for FY{target_year}."
                )
        
        return truth

    def _extract_year(self, year_str: Any) -> int | None:
        """Extract year from various formats."""
        if year_str is None:
            return None
        if isinstance(year_str, int):
            return year_str
        import re
        match = re.search(r"(\d{4})", str(year_str))
        return int(match.group(1)) if match else None

    def normalize_to_usd(
        self, value: float | int, unit: str
    ) -> tuple[float, str]:
        """Normalize values to USD with proper unit handling."""
        unit_lower = str(unit).lower()
        if "billion" in unit_lower:
            return float(value) * 1_000_000_000, "USD"
        elif "million" in unit_lower:
            return float(value) * 1_000_000, "USD"
        elif "thousand" in unit_lower:
            return float(value) * 1_000, "USD"
        elif unit_lower in ("usd", "$", "dollars"):
            return float(value), "USD"
        else:
            # Assume as-is, try to detect
            return float(value), "USD"

    def verify_metric(
        self,
        metric_name: str,
        extracted_value: float | int | None,
        extracted_unit: str,
        source_metric: dict[str, Any] | None,
        extraction_confidence: float | None = None,
        statement_reference: str | None = None,
    ) -> VerificationResult:
        """Verify a single metric against source. Never trust confidence alone."""
        issues = []
        
        if source_metric is None:
            issues.append(f"No source data found for {metric_name}")
            return VerificationResult(
                metric_name=metric_name,
                role=None,
                extracted_value=float(extracted_value) if extracted_value is not None else None,
                extracted_unit=extracted_unit,
                source_value=None,
                source_unit="USD Millions",
                source_value_usd=None,
                extracted_value_usd=None,
                match=False,
                verified=False,
                source_verified=False,
                extraction_confidence=extraction_confidence,
                source_reference={},
                filing_date=None,
                period_end=None,
                page_reference=None,
                statement_reference=statement_reference,
                tolerance_usd=0.0,
                difference_usd=None,
                issues=issues,
            )

        source_value_millions = source_metric.get("value")
        source_raw_value = source_metric.get("raw_value")
        source_unit = source_metric.get("unit", "USD Millions")
        filing_date = source_metric.get("filed")
        period_end = source_metric.get("end")
        
        # Calculate source in USD
        if source_raw_value is not None:
            source_value_usd = float(source_raw_value)
        elif source_value_millions is not None:
            source_value_usd = float(source_value_millions) * 1_000_000
        else:
            source_value_usd = None

        # Calculate extracted in USD
        extracted_value_usd = None
        if extracted_value is not None:
            extracted_value_usd, _ = self.normalize_to_usd(
                extracted_value, extracted_unit
            )

        # Calculate tolerance
        tolerance_usd = 0.0
        if source_value_usd is not None:
            tolerance_usd = max(
                abs(source_value_usd) * (self.tolerance_pct / 100.0),
                self.MIN_TOLERANCE_USD,
            )

        # Check match - ONLY based on actual values, never on confidence
        match = False
        difference_usd = None
        if extracted_value_usd is not None and source_value_usd is not None:
            difference_usd = extracted_value_usd - source_value_usd
            match = abs(difference_usd) <= tolerance_usd
            if not match:
                issues.append(
                    f"Value mismatch: extracted {extracted_value_usd:,.0f} USD vs "
                    f"source {source_value_usd:,.0f} USD (diff {difference_usd:,.0f} USD)"
                )
        else:
            issues.append("Cannot compare - missing extracted or source value")

        source_reference = {
            "tag": source_metric.get("tag"),
            "accession_number": source_metric.get("accession_number"),
            "frame": source_metric.get("frame"),
            "fy": source_metric.get("fy"),
            "fp": source_metric.get("fp"),
            "raw_value": source_metric.get("raw_value"),
            "computed": source_metric.get("computed", False),
        }

        # CRITICAL: verified is True ONLY if source-verified, never from confidence
        source_verified = match and extracted_value_usd is not None and source_value_usd is not None
        verified = source_verified  # Separate from extraction_confidence

        return VerificationResult(
            metric_name=metric_name,
            role=None,
            extracted_value=float(extracted_value) if extracted_value is not None else None,
            extracted_unit=extracted_unit,
            source_value=float(source_value_millions) if source_value_millions is not None else None,
            source_unit=source_unit,
            source_value_usd=source_value_usd,
            extracted_value_usd=extracted_value_usd,
            match=match,
            verified=verified,
            source_verified=source_verified,
            extraction_confidence=extraction_confidence,  # Kept separate
            source_reference=source_reference,
            filing_date=filing_date,
            period_end=period_end,
            page_reference=None,  # Can be enhanced with HTML parsing
            statement_reference=statement_reference,
            tolerance_usd=tolerance_usd,
            difference_usd=difference_usd,
            issues=issues,
        )

    def validate_balance_sheet(
        self, metrics: dict[str, VerificationResult]
    ) -> BalanceSheetValidation:
        """Validate Total Assets = Total Liabilities + Shareholders' Equity."""
        issues = []
        
        assets = metrics.get("Total Assets")
        liabilities = metrics.get("Total Liabilities")
        equity = metrics.get("Shareholders' Equity")
        
        assets_val = assets.source_value_usd if assets and assets.source_verified else (assets.extracted_value_usd if assets else None)
        liabilities_val = liabilities.source_value_usd if liabilities and liabilities.source_verified else (liabilities.extracted_value_usd if liabilities else None)
        equity_val = equity.source_value_usd if equity and equity.source_verified else (equity.extracted_value_usd if equity else None)
        
        if assets_val is None or liabilities_val is None or equity_val is None:
            issues.append("Missing values for balance sheet validation")
            return BalanceSheetValidation(
                valid=False,
                total_assets=assets_val,
                total_liabilities=liabilities_val,
                total_equity=equity_val,
                calculated_assets=None,
                difference=None,
                tolerance=0.0,
                issues=issues,
            )
        
        calculated_assets = liabilities_val + equity_val
        difference = assets_val - calculated_assets
        tolerance = max(abs(assets_val) * 0.001, 1_000_000)  # 0.1% or $1M
        
        valid = abs(difference) <= tolerance
        if not valid:
            issues.append(
                f"Balance sheet does not balance: Assets ({assets_val:,.0f}) != "
                f"Liabilities+Equity ({calculated_assets:,.0f}), diff={difference:,.0f}"
            )
        
        return BalanceSheetValidation(
            valid=valid,
            total_assets=assets_val,
            total_liabilities=liabilities_val,
            total_equity=equity_val,
            calculated_assets=calculated_assets,
            difference=difference,
            tolerance=tolerance,
            issues=issues,
        )

    def generate_report(
        self,
        company_name: str,
        cik: str,
        source_truth: dict[str, Any],
        verification_results: list[VerificationResult],
    ) -> AuditVerificationReport:
        """Generate complete verification report."""
        metrics_dict = {r.metric_name: r for r in verification_results}
        balance_validation = self.validate_balance_sheet(metrics_dict)
        
        verified_count = sum(1 for r in verification_results if r.source_verified)
        total_count = len(verification_results)
        
        # Calculate accuracy - never > 100, never based on confidence
        if total_count > 0:
            overall_accuracy = (verified_count / total_count) * 100.0
        else:
            overall_accuracy = 0.0
        
        # Clamp to valid range
        overall_accuracy = max(0.0, min(100.0, overall_accuracy))
        
        issues = []
        if balance_validation and not balance_validation.valid:
            issues.extend(balance_validation.issues)
        for r in verification_results:
            issues.extend(r.issues)
        
        return AuditVerificationReport(
            company_name=company_name,
            cik=source_truth["cik"],
            fiscal_year=source_truth["fiscal_year"],
            period_end=source_truth["period_end"],
            filing_date=source_truth["filing_date"],
            accession_number=source_truth["accession_number"],
            source=source_truth["source"],
            filing_url=None,
            metrics=verification_results,
            balance_sheet_validation=balance_validation,
            overall_accuracy=overall_accuracy,
            verified_count=verified_count,
            total_count=total_count,
            issues=issues,
        )
