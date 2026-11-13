from app import confidence


def test_all_signals_one_gives_one():
    signals = {
        "snippet_grounding": 1.0,
        "concept_match": 1.0,
        "unit_consistency": 1.0,
        "period_consistency": 1.0,
        "column_period": 1.0,
        "cross_statement": 1.0,
        "prior_year_sanity": 1.0,
        "extractor_agreement": 1.0,
        "llm_confidence": 1.0,
    }
    weights = {k: 1.0 for k in signals}
    result = confidence.computeConfidence(signals, weights)
    assert result["score"] == 1.0


def test_missing_signal_renormalizes():
    signals = {"snippet_grounding": 1.0, "concept_match": 1.0}
    weights = {"snippet_grounding": 0.5, "concept_match": 0.5}
    result = confidence.computeConfidence(signals, weights)
    assert abs(result["score"] - 1.0) < 1e-9


def test_grounding_zero_caps_at_fifty():
    signals = {"snippet_grounding": 0.0, "concept_match": 1.0}
    weights = {"snippet_grounding": 1.0, "concept_match": 1.0}
    result = confidence.computeConfidence(signals, weights)
    assert result["score"] <= 0.50


def test_extractor_disagreement_caps_at_sixty():
    signals = {"extractor_agreement": 0.0, "concept_match": 1.0}
    weights = {"extractor_agreement": 1.0, "concept_match": 1.0}
    result = confidence.computeConfidence(signals, weights)
    assert result["score"] <= 0.60


def test_metric_confidence_applies_hard_caps():
    """The metric-level scorer must honor the hard caps instead of only the
    pure-functions helper: an ungrounded value caps at 0.50 and an extractor
    disagreement caps at 0.60, otherwise good-looking signals hide evidence gaps.
    """
    metric = confidence.ExtractedMetric(
        metric_name="Revenue",
        value=391035.0,
        unit="USD Millions",
        period="FY2024",
        source_snippet="Net sales for fiscal years ended 2020 through 2023",
        confidence=0.95,
        extractor_agreement="disagree",
    )
    result = confidence.compute_confidence(metric, filing_metadata={"fiscal_year": "FY2024"})
    assert result["score"] <= 0.60
    assert metric.confidence_score <= 0.60
