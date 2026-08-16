from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class AssertionPayload:
    text: str
    confidence: float | None
    evidence: Mapping[str, Any] | None


def normalize_assertion(
    claim: Any,
    parallel_evidence: Mapping[str, Any] | None = None,
) -> AssertionPayload | None:
    """Normalize an A5 assertion without weakening its evidence contract.

    Current A5 keeps assertion text and validated evidence in parallel fields.
    A6 also accepts an evidence-backed nested object so the renderer remains
    compatible with a future contract change. A present claim never receives
    fabricated evidence: absent evidence remains ``None`` and is surfaced by
    the UI as a traceability error.
    """
    if claim is None or claim == "" or claim == []:
        return None

    if isinstance(claim, Mapping):
        text = str(claim.get("text") or "")
        raw_confidence = claim.get("confidence")
        confidence = float(raw_confidence) if raw_confidence is not None else None
        embedded = claim.get("evidence")
        evidence = embedded if isinstance(embedded, Mapping) else parallel_evidence
        return AssertionPayload(text=text, confidence=confidence, evidence=evidence)

    return AssertionPayload(text=str(claim), confidence=None, evidence=parallel_evidence)


def evidence_message_ids(evidence: Mapping[str, Any] | None) -> tuple[str, ...]:
    if not isinstance(evidence, Mapping):
        return ()
    raw = evidence.get("message_ids")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(value) for value in raw)


def evidence_metrics(evidence: Mapping[str, Any] | None) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(evidence, Mapping):
        return ()
    raw = evidence.get("metrics")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(item for item in raw if isinstance(item, Mapping))


def has_traceable_evidence(evidence: Mapping[str, Any] | None) -> bool:
    return bool(evidence_message_ids(evidence) or evidence_metrics(evidence))
