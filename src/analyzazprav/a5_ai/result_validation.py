from __future__ import annotations

from .models import AIAnalysisResult, AnalysisContext, EvidenceRef
from .validator import MAX_EVIDENCE_EXCERPT_CHARS, ValidationError


def _safe_excerpt(text: str) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= MAX_EVIDENCE_EXCERPT_CHARS:
        return normalized
    return normalized[: MAX_EVIDENCE_EXCERPT_CHARS - 1].rstrip() + "…"


def _validate_score(value: object, path: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{path} must be a number")
    if not 0.0 <= float(value) <= 1.0:
        raise ValidationError(f"{path} must be between 0 and 1")


def _validate_evidence(
    evidence: EvidenceRef | None,
    *,
    path: str,
    context: AnalysisContext,
    require_nonempty: bool = True,
) -> EvidenceRef:
    if evidence is None:
        raise ValidationError(f"{path} is missing")

    ids = tuple(evidence.message_ids)
    if require_nonempty and not ids:
        raise ValidationError(f"{path}.message_ids must not be empty")
    if len(ids) != len(set(ids)):
        raise ValidationError(f"{path}.message_ids contains duplicates")

    by_id = {message.id: message for message in context.messages}
    if len(by_id) != len(context.messages):
        raise ValidationError("current context contains duplicate message IDs")
    missing = [message_id for message_id in ids if message_id not in by_id]
    if missing:
        raise ValidationError(
            f"{path}.message_ids reference messages outside current context: {missing}"
        )

    snapshots = tuple(evidence.messages)
    snapshot_ids = tuple(snapshot.message_id for snapshot in snapshots)
    if snapshot_ids != ids:
        raise ValidationError(
            f"{path}.messages do not exactly cover message_ids in the same order"
        )
    for snapshot in snapshots:
        source = by_id[snapshot.message_id]
        if snapshot.timestamp != source.timestamp.isoformat():
            raise ValidationError(
                f"{path}.messages[{snapshot.message_id}].timestamp differs from source"
            )
        if snapshot.sender_id != source.participant_id:
            raise ValidationError(
                f"{path}.messages[{snapshot.message_id}].sender_id differs from source"
            )
        if snapshot.excerpt != _safe_excerpt(source.text):
            raise ValidationError(
                f"{path}.messages[{snapshot.message_id}].excerpt differs from source"
            )

    metrics_by_phase = {
        "before": context.metrics_before,
        "during": context.metrics_during,
        "after": context.metrics_after,
    }
    seen_metrics: set[tuple[str, str]] = set()
    for metric in evidence.metrics:
        key = (metric.phase, metric.name)
        if key in seen_metrics:
            raise ValidationError(f"{path}.metrics contains duplicate reference {key}")
        seen_metrics.add(key)
        if metric.phase not in metrics_by_phase:
            raise ValidationError(f"{path}.metrics has invalid phase {metric.phase!r}")
        phase_metrics = metrics_by_phase[metric.phase]
        if metric.name not in phase_metrics:
            raise ValidationError(
                f"{path}.metrics references metric outside current context: {metric.phase}.{metric.name}"
            )
        if float(metric.value) != float(phase_metrics[metric.name]):
            raise ValidationError(
                f"{path}.metrics value differs from current deterministic context: "
                f"{metric.phase}.{metric.name}"
            )
    return evidence


def validate_resolved_result(
    result: AIAnalysisResult,
    context: AnalysisContext,
) -> None:
    """Revalidate a resolved/cached A5 result against the current source context.

    Provider output is validated before persistence. This second validator exists
    specifically for the trust boundary introduced by persisted local cache: a
    manually altered or stale result_json must never bypass source/evidence checks
    merely because its context_hash key still exists.
    """

    _validate_score(result.overall_confidence, "overall_confidence")
    _validate_evidence(
        result.summary_evidence,
        path="summary_evidence",
        context=context,
    )

    for index, observation in enumerate(result.observations):
        _validate_score(observation.strength, f"observations[{index}].strength")
        _validate_evidence(
            observation.evidence,
            path=f"observations[{index}].evidence",
            context=context,
        )

    for index, interpretation in enumerate(result.interpretations):
        _validate_score(
            interpretation.confidence,
            f"interpretations[{index}].confidence",
        )
        evidence = _validate_evidence(
            interpretation.evidence,
            path=f"interpretations[{index}].evidence",
            context=context,
        )
        if tuple(interpretation.evidence_message_ids) != tuple(evidence.message_ids):
            raise ValidationError(
                f"interpretations[{index}].evidence_message_ids differ from resolved evidence"
            )

    for index, pattern in enumerate(result.patterns):
        _validate_score(pattern.confidence, f"patterns[{index}].confidence")
        if pattern.occurrences is not None and (
            isinstance(pattern.occurrences, bool)
            or not isinstance(pattern.occurrences, int)
            or pattern.occurrences < 0
        ):
            raise ValidationError(
                f"patterns[{index}].occurrences must be a non-negative integer or null"
            )
        evidence = _validate_evidence(
            pattern.evidence,
            path=f"patterns[{index}].evidence",
            context=context,
        )
        if tuple(pattern.evidence_message_ids) != tuple(evidence.message_ids):
            raise ValidationError(
                f"patterns[{index}].evidence_message_ids differ from resolved evidence"
            )

    if len(result.turning_points) != len(result.turning_point_evidence):
        raise ValidationError(
            "turning point evidence count does not match turning point count"
        )
    for index, evidence in enumerate(result.turning_point_evidence):
        _validate_evidence(
            evidence,
            path=f"turning_point_evidence[{index}]",
            context=context,
        )

    optional_claims = (
        ("participant_p1", result.participant_p1, result.participant_p1_evidence),
        ("participant_p2", result.participant_p2, result.participant_p2_evidence),
        ("shared_dynamic", result.shared_dynamic, result.shared_dynamic_evidence),
    )
    for path, text, evidence in optional_claims:
        if text is None:
            if evidence is not None:
                raise ValidationError(f"{path} is null but evidence is present")
            continue
        _validate_evidence(
            evidence,
            path=f"{path}_evidence",
            context=context,
        )

    if any(not isinstance(value, str) for value in result.alternative_explanations):
        raise ValidationError("alternative_explanations must contain strings")
    if any(not isinstance(value, str) for value in result.unknowns):
        raise ValidationError("unknowns must contain strings")
