from __future__ import annotations

from typing import Any, Mapping

from .models import (
    AIAnalysisResult,
    AnalysisContext,
    EvidenceRef,
    Interpretation,
    MessageEvidence,
    MetricEvidence,
    Observation,
    Pattern,
)


class ValidationError(ValueError):
    pass


MAX_EVIDENCE_EXCERPT_CHARS = 240
_VALID_METRIC_PHASES = {"before", "during", "after"}


def _require_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError(f"{path} must be an object")
    return value


def _require_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValidationError(f"{path} must be an array")
    return value


def _require_str(value: Any, path: str, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{path} must be a string")
    return value


def _score(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{path} must be a number")
    number = float(value)
    if not 0 <= number <= 1:
        raise ValidationError(f"{path} must be between 0 and 1")
    return number


def _message_ids(value: Any, path: str, allowed_message_ids: set[str], *, require_nonempty: bool = True) -> tuple[str, ...]:
    raw = _require_list(value, path)
    ids: list[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, str):
            raise ValidationError(f"{path}[{index}] must be a string")
        if item not in allowed_message_ids:
            raise ValidationError(f"{path}[{index}] references message outside supplied context: {item}")
        ids.append(item)
    if require_nonempty and not ids:
        raise ValidationError(f"{path} must contain at least one evidence message ID")
    return tuple(dict.fromkeys(ids))


def _safe_excerpt(text: str) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= MAX_EVIDENCE_EXCERPT_CHARS:
        return normalized
    return normalized[: MAX_EVIDENCE_EXCERPT_CHARS - 1].rstrip() + "…"


def _metric_refs(value: Any, path: str, context: AnalysisContext) -> tuple[MetricEvidence, ...]:
    if value is None:
        return ()
    raw = _require_list(value, path)
    metrics_by_phase = {
        "before": context.metrics_before,
        "during": context.metrics_during,
        "after": context.metrics_after,
    }
    refs: list[MetricEvidence] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(raw):
        obj = _require_mapping(item, f"{path}[{index}]")
        phase = _require_str(obj.get("phase"), f"{path}[{index}].phase")
        name = _require_str(obj.get("name"), f"{path}[{index}].name")
        if phase not in _VALID_METRIC_PHASES:
            raise ValidationError(f"{path}[{index}].phase must be before, during or after")
        phase_metrics = metrics_by_phase[phase]
        if name not in phase_metrics:
            raise ValidationError(f"{path}[{index}] references metric outside supplied context: {phase}.{name}")
        key = (phase, name)
        if key in seen:
            continue
        seen.add(key)
        refs.append(MetricEvidence(phase=phase, name=name, value=float(phase_metrics[name])))
    return tuple(refs)


def _evidence_ref(
    message_ids: tuple[str, ...],
    description: str,
    context: AnalysisContext,
    metric_refs: Any = None,
    *,
    metric_path: str = "evidence.metric_refs",
) -> EvidenceRef:
    by_id = {message.id: message for message in context.messages}
    snapshots = tuple(
        MessageEvidence(
            message_id=message_id,
            timestamp=by_id[message_id].timestamp.isoformat(),
            sender_id=by_id[message_id].participant_id,
            excerpt=_safe_excerpt(by_id[message_id].text),
        )
        for message_id in message_ids
    )
    return EvidenceRef(
        message_ids=message_ids,
        description=description,
        messages=snapshots,
        metrics=_metric_refs(metric_refs, metric_path, context),
    )


def _claim_parts(value: Any, path: str, context: AnalysisContext, allowed: set[str], *, allow_none: bool = False) -> tuple[str | None, EvidenceRef | None]:
    if value is None and allow_none:
        return None, None
    obj = _require_mapping(value, path)
    text = _require_str(obj.get("text"), f"{path}.text")
    _score(obj.get("confidence"), f"{path}.confidence")
    evidence_obj = _require_mapping(obj.get("evidence"), f"{path}.evidence")
    ids = _message_ids(evidence_obj.get("message_ids"), f"{path}.evidence.message_ids", allowed)
    description = _require_str(evidence_obj.get("description", ""), f"{path}.evidence.description")
    evidence = _evidence_ref(
        ids,
        description,
        context,
        evidence_obj.get("metric_refs"),
        metric_path=f"{path}.evidence.metric_refs",
    )
    return text, evidence


def parse_and_validate_result(payload: Mapping[str, Any], context: AnalysisContext) -> AIAnalysisResult:
    allowed = {message.id for message in context.messages}
    root = _require_mapping(payload, "result")
    summary, summary_evidence = _claim_parts(root.get("summary"), "summary", context, allowed)
    assert summary is not None and summary_evidence is not None

    observations: list[Observation] = []
    for i, raw in enumerate(_require_list(root.get("observations", []), "observations")):
        obj = _require_mapping(raw, f"observations[{i}]")
        evidence_obj = _require_mapping(obj.get("evidence"), f"observations[{i}].evidence")
        ids = _message_ids(evidence_obj.get("message_ids"), f"observations[{i}].evidence.message_ids", allowed)
        description = _require_str(evidence_obj.get("description", ""), f"observations[{i}].evidence.description")
        observations.append(Observation(
            text=_require_str(obj.get("text"), f"observations[{i}].text"),
            evidence=_evidence_ref(ids, description, context, evidence_obj.get("metric_refs"), metric_path=f"observations[{i}].evidence.metric_refs"),
            strength=_score(obj.get("strength"), f"observations[{i}].strength"),
        ))

    interpretations: list[Interpretation] = []
    for i, raw in enumerate(_require_list(root.get("interpretations", []), "interpretations")):
        obj = _require_mapping(raw, f"interpretations[{i}]")
        ids = _message_ids(obj.get("evidence_message_ids"), f"interpretations[{i}].evidence_message_ids", allowed)
        evidence = _evidence_ref(ids, "", context, obj.get("metric_refs"), metric_path=f"interpretations[{i}].metric_refs")
        interpretations.append(Interpretation(
            text=_require_str(obj.get("text"), f"interpretations[{i}].text"),
            evidence_message_ids=ids,
            confidence=_score(obj.get("confidence"), f"interpretations[{i}].confidence"),
            evidence=evidence,
        ))

    patterns: list[Pattern] = []
    for i, raw in enumerate(_require_list(root.get("patterns", []), "patterns")):
        obj = _require_mapping(raw, f"patterns[{i}]")
        occurrences = obj.get("occurrences")
        if occurrences is not None and (isinstance(occurrences, bool) or not isinstance(occurrences, int) or occurrences < 0):
            raise ValidationError(f"patterns[{i}].occurrences must be a non-negative integer or null")
        ids = _message_ids(obj.get("evidence_message_ids"), f"patterns[{i}].evidence_message_ids", allowed)
        evidence = _evidence_ref(ids, "", context, obj.get("metric_refs"), metric_path=f"patterns[{i}].metric_refs")
        patterns.append(Pattern(
            pattern_type=_require_str(obj.get("pattern_type"), f"patterns[{i}].pattern_type"),
            description=_require_str(obj.get("description"), f"patterns[{i}].description"),
            occurrences=occurrences,
            confidence=_score(obj.get("confidence"), f"patterns[{i}].confidence"),
            evidence_message_ids=ids,
            evidence=evidence,
        ))

    turning_points: list[str] = []
    turning_point_evidence: list[EvidenceRef] = []
    for i, item in enumerate(_require_list(root.get("turning_points", []), "turning_points")):
        text, evidence = _claim_parts(item, f"turning_points[{i}]", context, allowed)
        assert text is not None and evidence is not None
        turning_points.append(text)
        turning_point_evidence.append(evidence)

    participant_p1, participant_p1_evidence = _claim_parts(root.get("participant_p1"), "participant_p1", context, allowed, allow_none=True)
    participant_p2, participant_p2_evidence = _claim_parts(root.get("participant_p2"), "participant_p2", context, allowed, allow_none=True)
    shared_dynamic, shared_dynamic_evidence = _claim_parts(root.get("shared_dynamic"), "shared_dynamic", context, allowed, allow_none=True)

    def strings(name: str) -> tuple[str, ...]:
        return tuple(_require_str(item, f"{name}[{i}]") for i, item in enumerate(_require_list(root.get(name, []), name)))

    return AIAnalysisResult(
        summary=summary,
        summary_evidence=summary_evidence,
        observations=tuple(observations),
        interpretations=tuple(interpretations),
        patterns=tuple(patterns),
        turning_points=tuple(turning_points),
        turning_point_evidence=tuple(turning_point_evidence),
        participant_p1=participant_p1,
        participant_p1_evidence=participant_p1_evidence,
        participant_p2=participant_p2,
        participant_p2_evidence=participant_p2_evidence,
        shared_dynamic=shared_dynamic,
        shared_dynamic_evidence=shared_dynamic_evidence,
        alternative_explanations=strings("alternative_explanations"),
        unknowns=strings("unknowns"),
        overall_confidence=_score(root.get("overall_confidence"), "overall_confidence"),
    )
