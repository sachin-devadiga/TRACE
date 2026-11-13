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
