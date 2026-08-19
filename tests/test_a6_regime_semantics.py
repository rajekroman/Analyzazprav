from a6.semantics import candidate_semantics


def test_published_regime_alias_uses_safe_dyadic_semantics() -> None:
    published = candidate_semantics("regime")
    dyadic = candidate_semantics("dyadic_regime")

    assert published == dyadic
    assert published.label == "Kandidátní komunikační režim"
    assert published.fact_level == "pattern_candidate"
    assert "motivace" in published.explanation
    assert "stavu vztahu" in published.explanation
