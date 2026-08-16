from __future__ import annotations

from typing import Any, Iterable

from analyzazprav.a5_ai.models import (
    AIAnalysisResult,
    AnalysisContext,
    EvidenceRef,
)
from .staging import STATUS_FAIL, STATUS_PASS


def _all_evidence(result: AIAnalysisResult) -> Iterable[tuple[str, EvidenceRef]]:
    yield "summary", result.summary_evidence
    for index, item in enumerate(result.observations):
        yield f"observations[{index}]", item.evidence
    for index, item in enumerate(result.interpretations):
        if item.evidence is not None:
            yield f"interpretations[{index}]", item.evidence
    for index, item in enumerate(result.patterns):
        if item.evidence is not None:
            yield f"patterns[{index}]", item.evidence
    for index, item in enumerate(result.turning_point_evidence):
        yield f"turning_points[{index}]", item
    if result.participant_p1 is not None and result.participant_p1_evidence is not None:
        yield "participant_p1", result.participant_p1_evidence
    if result.participant_p2 is not None and result.participant_p2_evidence is not None:
        yield "participant_p2", result.participant_p2_evidence
    if result.shared_dynamic is not None and result.shared_dynamic_evidence is not None:
        yield "shared_dynamic", result.shared_dynamic_evidence


def validate_a5_evidence_chain(
    context: AnalysisContext,
    result: AIAnalysisResult,
) -> dict[str, Any]:
    """Independently audit A5's post-validation evidence snapshots.

    This oracle does not call A5's result validator. It compares the already
    materialized result evidence back to the immutable AnalysisContext and A4
    provenance metadata.
    """

    issues: list[dict[str, str]] = []
    checks: dict[str, Any] = {}

    def fail(code: str, detail: str) -> None:
        issues.append({"severity": "ERROR", "code": code, "detail": detail})

    if context.missing_evidence_message_ids:
        fail(
            "A5_CONTEXT_MISSING_CANDIDATE_EVIDENCE",
            ", ".join(context.missing_evidence_message_ids),
        )

    by_id = {message.id: message for message in context.messages}
    if len(by_id) != len(context.messages):
        fail(
            "A5_CONTEXT_MESSAGE_ID_DUPLICATE",
            "AnalysisContext contains duplicate canonical message IDs.",
        )

    if context.omitted_message_count:
        if context.omitted_message_count != len(context.omitted_message_ids):
            fail(
                "A5_CONTEXT_OMISSION_COUNT_MISMATCH",
                "omitted_message_count does not match omitted_message_ids.",
            )
        if not context.omitted_message_ids_sha256:
            fail(
                "A5_CONTEXT_OMISSION_HASH_MISSING",
                "Reduced context does not carry an omission fingerprint.",
            )
        if not any("omitted" in warning.lower() for warning in context.quality_warnings):
            fail(
                "A5_CONTEXT_REDUCTION_WARNING_MISSING",
                "Reduced context is not disclosed in quality_warnings.",
            )

    evidence_rows = list(_all_evidence(result))
    checks["assertion_evidence_refs"] = len(evidence_rows)
    checks["context_message_count"] = len(context.messages)
    checks["context_omitted_message_count"] = context.omitted_message_count

    expected_metric_provenance = {
        "analytics_run_id": _text(context.candidate_provenance.get("analytics_run_id")),
        "analytics_version": _text(context.candidate_provenance.get("analytics_version")),
        "analysis_signature": _text(context.candidate_provenance.get("analysis_signature")),
        "source_fingerprint": _text(context.candidate_provenance.get("source_fingerprint")),
        "processing_run_id": _text(context.candidate_provenance.get("processing_run_id")),
    }
    metrics_by_phase = {
        "before": context.metrics_before,
        "during": context.metrics_during,
        "after": context.metrics_after,
    }

    message_snapshot_count = 0
    metric_snapshot_count = 0
    for path, evidence in evidence_rows:
        if not evidence.message_ids:
            fail("A5_ASSERTION_EVIDENCE_EMPTY", f"{path} has no message IDs.")
        if tuple(item.message_id for item in evidence.messages) != evidence.message_ids:
            fail(
                "A5_MESSAGE_SNAPSHOT_ID_MISMATCH",
                f"{path} snapshots do not exactly match evidence.message_ids.",
            )
        for snapshot in evidence.messages:
            message_snapshot_count += 1
            source = by_id.get(snapshot.message_id)
            if source is None:
                fail(
                    "A5_MESSAGE_EVIDENCE_OUTSIDE_CONTEXT",
                    f"{path} references {snapshot.message_id!r} outside context.",
                )
                continue
            expected = (
                source.timestamp.isoformat(),
                source.participant_id,
                source.membership_id,
                source.source_record_keys,
                source.source_snapshot_keys,
                source.source_parser_versions,
            )
            actual = (
                snapshot.timestamp,
                snapshot.sender_id,
                snapshot.membership_id,
                snapshot.source_record_keys,
                snapshot.source_snapshot_keys,
                snapshot.source_parser_versions,
            )
            if actual != expected:
                fail(
                    "A5_MESSAGE_PROVENANCE_MISMATCH",
                    f"{path} evidence for {snapshot.message_id} differs from AnalysisContext.",
                )
            if source.source_record_keys and not snapshot.source_record_keys:
                fail(
                    "A5_SOURCE_RECORD_PROVENANCE_DROPPED",
                    f"{path} dropped source_record_key provenance for {snapshot.message_id}.",
                )

        for metric in evidence.metrics:
            metric_snapshot_count += 1
            phase_metrics = metrics_by_phase.get(metric.phase)
            if phase_metrics is None or metric.name not in phase_metrics:
                fail(
                    "A5_METRIC_OUTSIDE_CONTEXT",
                    f"{path} metric {metric.phase}.{metric.name} is outside context.",
                )
                continue
            if float(phase_metrics[metric.name]) != metric.value:
                fail(
                    "A5_METRIC_VALUE_MISMATCH",
                    f"{path} metric {metric.phase}.{metric.name} changed value.",
                )
            actual_provenance = {
                "analytics_run_id": metric.analytics_run_id,
                "analytics_version": metric.analytics_version,
                "analysis_signature": metric.analysis_signature,
                "source_fingerprint": metric.source_fingerprint,
                "processing_run_id": metric.processing_run_id,
            }
            if actual_provenance != expected_metric_provenance:
                fail(
                    "A5_METRIC_PROVENANCE_MISMATCH",
                    f"{path} metric provenance differs from candidate provenance.",
                )

    checks["message_evidence_snapshots"] = message_snapshot_count
    checks["metric_evidence_snapshots"] = metric_snapshot_count

    # Assertion-bearing optional synthesis fields may not exist without their
    # parallel evidence refs.
    for field, value, evidence in (
        ("participant_p1", result.participant_p1, result.participant_p1_evidence),
        ("participant_p2", result.participant_p2, result.participant_p2_evidence),
        ("shared_dynamic", result.shared_dynamic, result.shared_dynamic_evidence),
    ):
        if value is not None and evidence is None:
            fail("A5_ASSERTION_PARALLEL_EVIDENCE_MISSING", f"{field} has text but no evidence ref.")
    if len(result.turning_points) != len(result.turning_point_evidence):
        fail(
            "A5_TURNING_POINT_EVIDENCE_COUNT_MISMATCH",
            "turning point texts and evidence refs have different lengths.",
        )

    return {
        "schema_version": 1,
        "status": STATUS_FAIL if issues else STATUS_PASS,
        "checks": checks,
        "counts": {"errors": len(issues)},
        "issues": issues,
    }


def _text(value: object) -> str | None:
    return None if value is None else str(value)
