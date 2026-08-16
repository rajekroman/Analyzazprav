from a6.semantics import candidate_semantics


def test_conflict_candidate_is_not_fact():
    value = candidate_semantics("conflict")
    assert value.fact_level == "pattern_candidate"
    assert "Není" in value.explanation


def test_engagement_is_explicitly_heuristic():
    value = candidate_semantics("engagement_signal")
    assert value.fact_level == "heuristic_signal"
    assert "psychologickým" in value.explanation


def test_change_point_does_not_claim_cause():
    value = candidate_semantics("change_point")
    assert value.fact_level == "metric_pattern"
    assert "Příčinu" in value.explanation
