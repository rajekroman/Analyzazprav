from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CandidateSemantics:
    label: str
    explanation: str
    fact_level: str


_SEMANTICS = {
    "conflict": CandidateSemantics(
        "Konfliktní kandidát",
        "Deterministický heuristický kandidát z A4. Není sám o sobě důkazem konfliktu; význam musí ověřit konkrétní zprávy.",
        "pattern_candidate",
    ),
    "engagement_signal": CandidateSemantics(
        "Signál engagementu",
        "Kompozitní A4 signál z měřitelných faktorů. Není psychologickým ani vztahovým faktem.",
        "heuristic_signal",
    ),
    "dyadic_regime": CandidateSemantics(
        "Kandidátní komunikační režim",
        "Operační A4 klasifikace změn dvou účastníků. Nejde o interpretaci motivace ani stavu vztahu.",
        "pattern_candidate",
    ),
    "change_point": CandidateSemantics(
        "Statistický bod změny",
        "Deterministický statistický kandidát změny metriky. Příčinu ani význam sám neurčuje.",
        "metric_pattern",
    ),
    "lexical_topic": CandidateSemantics(
        "Lexikální evidence tématu",
        "Opakovaný slovní/ngramový vzorec. Není sémantickým ani psychologickým závěrem bez kontroly zpráv.",
        "lexical_evidence",
    ),
}


def candidate_semantics(candidate_type: str | None) -> CandidateSemantics:
    key = (candidate_type or "").strip().casefold()
    if key == "regime":
        key = "dyadic_regime"
    return _SEMANTICS.get(
        key,
        CandidateSemantics(
            "Deterministický kandidát",
            "Tento výstup je výběrový nebo metrický signál. Jeho význam musí být doložen zdrojovými zprávami.",
            "candidate",
        ),
    )
