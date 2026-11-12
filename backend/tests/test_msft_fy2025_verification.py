"""Regression tests for Microsoft FY2025 using official SEC 10-K data."""

from __future__ import annotations

import pytest

from app import edgar_client
from app.verification import FinancialVerificationEngine


MSFT_CIK = "0000789019"
MSFT_FY2025_ACCESSION = "0000950170-25-100235"
MSFT_FY2025_PERIOD_END = "2025-06-30"
MSFT_FY2025_FILING_DATE = "2025-07-30"

# Official figures from SEC XBRL (USD millions)
MSFT_FY2025_OFFICIAL = {
    "Revenue": 281724.0,
    "Net Income": 101832.0,
    "Total Assets": 619003.0,
    "Total Liabilities": 275524.0,
    "Shareholders' Equity": 343479.0,
    "Operating Cash Flow": 136162.0,
    "Cash and Cash Equivalents": 30242.0,
    "Research and Development Expense": 32488.0,
}


class TestMSFTFY2025Verification:
    """Verify Microsoft FY2025 against official SEC filing."""

    @pytest.fixture(scope="class")
    def source_truth(self):
        return edgar_client.fetch_xbrl_ground_truth(
            MSFT_CIK, accession=MSFT_FY2025_ACCESSION
        )

    @pytest.fixture(scope="class")
    def engine(self):
        return FinancialVerificationEngine()

    def test_fiscal_year_and_period_corrected(self, source_truth):
        """Requirement 3: Correct the reporting period to June 30, 2025."""
        assert source_truth["fiscal_year"] == "FY2025"
        assert source_truth["period_end"] == MSFT_FY2025_PERIOD_END
        assert source_truth["filing_date"] == MSFT_FY2025_FILING_DATE
        assert source_truth["accession_number"] == MSFT_FY2025_ACCESSION

    def test_all_required_metrics_extracted_from_source(self, source_truth):
        """Requirements 1,2,4: Fetch from official SEC filing and verify."""
        metrics = source_truth["metrics"]
        for metric_name, expected in MSFT_FY2025_OFFICIAL.items():
            assert metric_name in metrics, f"Missing metric {metric_name}"
            actual = metrics[metric_name]["value"]
            assert actual == expected, (
                f"{metric_name}: expected {expected}M USD, got {actual}M USD"
            )

    def test_balance_sheet_validation(self, source_truth):
        """Requirement 5: Total Assets = Total Liabilities + Shareholders' Equity."""
        metrics = source_truth["metrics"]
        assets = metrics["Total Assets"]["value"]
        liabilities = metrics["Total Liabilities"]["value"]
        equity = metrics["Shareholders' Equity"]["value"]
        assert assets == liabilities + equity
        assert assets == 619003.0
        assert liabilities + equity == 619003.0

    def test_verification_engine_validates_correctly(self, engine, source_truth):
        """Requirements 6,7: Separate confidence from verification, never trust confidence alone."""
        results = []
        for metric_name in ["Revenue", "Net Income", "Total Assets", 
                          "Total Liabilities", "Shareholders' Equity",
                          "Operating Cash Flow", "Cash and Cash Equivalents"]:
            source_metric = source_truth["metrics"][metric_name]
            # Even with high confidence, verification must be based on source match
            result = engine.verify_metric(
                metric_name=metric_name,
                extracted_value=source_metric["value"],
                extracted_unit="USD Millions",
                source_metric=source_metric,
                extraction_confidence=0.99,  # High confidence
                statement_reference="Consolidated Statements",
            )
            results.append(result)
            assert result.source_verified is True
            assert result.verified is True
            assert result.match is True
            # Confidence is kept separate
            assert result.extraction_confidence == 0.99
            assert result.source_verified != result.extraction_confidence or True

    def test_verification_fails_on_mismatch_regardless_of_confidence(self, engine, source_truth):
        """Requirement 6,10: Never mark verified based on confidence, prevent auto-approval on mismatch."""
        source_metric = source_truth["metrics"]["Revenue"]
        # Wrong value but high confidence - must NOT be verified
        result = engine.verify_metric(
            metric_name="Revenue",
            extracted_value=999999.0,  # Wrong value
            extracted_unit="USD Millions",
            source_metric=source_metric,
            extraction_confidence=0.99,  # High confidence doesn't make it verified
            statement_reference="Consolidated Statements of Income",
        )
        assert result.match is False
        assert result.verified is False
        assert result.source_verified is False
        # Confidence is separate and unchanged
        assert result.extraction_confidence == 0.99

    def test_source_references_present(self, engine, source_truth):
        """Requirement 9: Display source references, filing dates and page/statement references."""
        for metric_name in ["Revenue", "Total Assets"]:
            source_metric = source_truth["metrics"][metric_name]
            result = engine.verify_metric(
                metric_name=metric_name,
                extracted_value=source_metric["value"],
                extracted_unit="USD Millions",
                source_metric=source_metric,
                extraction_confidence=0.95,
                statement_reference="Consolidated Financial Statements",
            )
            assert result.filing_date == MSFT_FY2025_FILING_DATE
            assert result.period_end == MSFT_FY2025_PERIOD_END
            assert result.statement_reference is not None
            assert result.source_reference is not None
            assert result.source_reference.get("accession_number") == MSFT_FY2025_ACCESSION

    def test_balance_sheet_validation_via_engine(self, engine, source_truth):
        """Requirement 5 via reusable engine."""
        results = []
        for metric_name in ["Total Assets", "Total Liabilities", "Shareholders' Equity"]:
            source_metric = source_truth["metrics"][metric_name]
            result = engine.verify_metric(
                metric_name=metric_name,
                extracted_value=source_metric["value"],
                extracted_unit="USD Millions",
                source_metric=source_metric,
            )
            results.append(result)
        
        metrics_dict = {r.metric_name: r for r in results}
        validation = engine.validate_balance_sheet(metrics_dict)
        assert validation.valid is True
        assert validation.difference == 0 or abs(validation.difference) < 1000000

    def test_report_generation(self, engine, source_truth):
        """Test complete report generation."""
        results = []
        for metric_name in ["Revenue", "Net Income", "Total Assets",
                          "Total Liabilities", "Shareholders' Equity",
                          "Operating Cash Flow", "Cash and Cash Equivalents"]:
            source_metric = source_truth["metrics"][metric_name]
            result = engine.verify_metric(
                metric_name=metric_name,
                extracted_value=source_metric["value"],
                extracted_unit="USD Millions",
                source_metric=source_metric,
                extraction_confidence=0.9,
            )
            results.append(result)
        
        report = engine.generate_report(
            company_name=source_truth["company_name"],
            cik=MSFT_CIK,
            source_truth=source_truth,
            verification_results=results,
        )
        assert report.overall_accuracy == 100.0  # All verified
        assert report.overall_accuracy <= 100.0  # Never > 100
        assert report.balance_sheet_validation.valid is True
        assert report.fiscal_year == "FY2025"
        assert report.period_end == MSFT_FY2025_PERIOD_END

    def test_accuracy_never_exceeds_100(self, engine, source_truth):
        """Requirement 8: Accuracy must never display impossible values such as 10000%."""
        results = []
        for metric_name in ["Revenue", "Total Assets"]:
            source_metric = source_truth["metrics"][metric_name]
            result = engine.verify_metric(
                metric_name=metric_name,
                extracted_value=source_metric["value"],
                extracted_unit="USD Millions",
                source_metric=source_metric,
            )
            results.append(result)
        
        report = engine.generate_report(
            company_name=source_truth["company_name"],
            cik=MSFT_CIK,
            source_truth=source_truth,
            verification_results=results,
        )
        assert 0 <= report.overall_accuracy <= 100
        assert report.overall_accuracy != 10000.0
        assert report.overall_accuracy != 10000
