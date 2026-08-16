from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from analyzazprav.a5_ai.integration_a6 import A6PacketError, messages_from_a6_packet
from analyzazprav.a5_ai.models import (
    AnalysisContext,
    AnalysisMode,
    AnalysisType,
    EvidenceBackedClaim,
    MessageRecord,
)
from analyzazprav.a5_ai.validator import ValidationError, parse_and_validate_result

VERDICT_VALID = "VALID"
VERDICT_INVALID = "INVALID"


def _claim(text: str, ids: list[str], confidence: float = 0.8, *, metric_refs: list[dict[str, str]] | None = None) -> dict[str, object]:
    return {
        "text": text,
        "confidence": confidence,
        "evidence": {
            "message_ids": ids,
            "description": "A7 pinned evidence claim",
            "metric_refs": metric_refs or [],
        },
    }


def audit_a5_contract() -> dict[str, object]:
    issues: list[dict[str, str]] = []

    duplicate_packet = {
        "schema_version": 1,
        "messages": [
            {"message_id": "1", "conversation_id": "c1", "sender": "p1", "timestamp": "2026-01-01T10:00:00Z", "text": "x"},
            {"message_id": "1", "conversation_id": "c1", "sender": "p2", "timestamp": "2026-01-01T10:01:00Z", "text": "y"},
        ],
    }
    try:
        messages_from_a6_packet(duplicate_packet)
    except A6PacketError:
        pass
    else:
        issues.append({
            "severity": "ERROR",
            "code": "A5_DUPLICATE_A6_MESSAGE_ID_ACCEPTED",
            "detail": "A5 accepted duplicate message identity from A6 packet",
        })

    messages = (
        MessageRecord(
            id="1",
            conversation_id="c1",
            participant_id="p1",
            timestamp=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
            text="  Evidence   text with   whitespace  ",
        ),
        MessageRecord(
            id="2",
            conversation_id="c1",
            participant_id="p2",
            timestamp=datetime(2026, 1, 1, 10, 1, tzinfo=timezone.utc),
            text="Reply",
        ),
    )
    context = AnalysisContext(
        conversation_id="c1",
        analysis_type=AnalysisType.SEGMENT,
        mode=AnalysisMode.BLIND,
        requested_start_ts=messages[0].timestamp,
        requested_end_ts=messages[-1].timestamp,
        context_start_ts=messages[0].timestamp,
        context_end_ts=messages[-1].timestamp,
        cutoff_ts=messages[-1].timestamp,
        messages=messages,
        evidence_message_ids=("1", "2"),
        metrics_during={"median_response_latency_seconds": 60.0},
    )
    metric_ref = [{"phase": "during", "name": "median_response_latency_seconds"}]
    payload = {
        "summary": _claim("The interaction contains a response.", ["1", "2"], metric_refs=metric_ref),
        "observations": [
            {
                "text": "A reply follows the first message.",
                "evidence": {
                    "message_ids": ["1", "2"],
                    "description": "Two-message sequence",
                    "metric_refs": metric_ref,
                },
                "strength": 0.9,
            }
        ],
        "interpretations": [
            {
                "text": "This may indicate engagement.",
                "evidence_message_ids": ["1", "2"],
                "metric_refs": [],
                "confidence": 0.6,
            }
        ],
        "patterns": [
            {
                "pattern_type": "reply",
                "description": "Reply pattern",
                "occurrences": 1,
                "confidence": 0.7,
                "evidence_message_ids": ["1", "2"],
                "metric_refs": [],
            }
        ],
        "turning_points": [_claim("A reply appears.", ["2"])],
        "participant_p1": _claim("P1 sends the opening message.", ["1"]),
        "participant_p2": _claim("P2 replies.", ["2"]),
        "shared_dynamic": _claim("The exchange is reciprocal in this segment.", ["1", "2"]),
        "alternative_explanations": ["The reply can be routine rather than relationally meaningful."],
        "unknowns": ["Intent is not directly observable."],
        "overall_confidence": 0.7,
    }

    try:
        result = parse_and_validate_result(payload, context)
    except ValidationError as exc:
        issues.append({
            "severity": "ERROR",
            "code": "A5_VALID_EVIDENCE_PAYLOAD_REJECTED",
            "detail": str(exc),
        })
        result = None

    if result is not None:
        evidence = result.observations[0].evidence
        expected_ids = ("1", "2")
        if evidence.message_ids != expected_ids or tuple(item.message_id for item in evidence.messages) != expected_ids:
            issues.append({"severity": "ERROR", "code": "A5_MESSAGE_EVIDENCE_ID_MISMATCH", "detail": "structured evidence snapshots do not preserve source IDs"})
        if evidence.messages[0].timestamp != messages[0].timestamp.isoformat():
            issues.append({"severity": "ERROR", "code": "A5_EVIDENCE_TIMESTAMP_MISMATCH", "detail": "timestamp was not derived exactly from AnalysisContext"})
        if evidence.messages[0].sender_id != "p1":
            issues.append({"severity": "ERROR", "code": "A5_EVIDENCE_SENDER_MISMATCH", "detail": "sender was not derived exactly from AnalysisContext"})
        if evidence.messages[0].excerpt != "Evidence text with whitespace":
            issues.append({"severity": "ERROR", "code": "A5_EVIDENCE_EXCERPT_MISMATCH", "detail": f"unexpected normalized excerpt: {evidence.messages[0].excerpt!r}"})
        if len(evidence.metrics) != 1 or evidence.metrics[0].value != 60.0:
            issues.append({"severity": "ERROR", "code": "A5_METRIC_EVIDENCE_VALUE_MISMATCH", "detail": "metric value was not enriched from deterministic context"})

        claims = [result.summary, *result.turning_points]
        claims.extend(item for item in (result.participant_p1, result.participant_p2, result.shared_dynamic) if item is not None)
        for index, claim in enumerate(claims):
            if not isinstance(claim, EvidenceBackedClaim):
                issues.append({"severity": "ERROR", "code": "A5_ASSERTION_NOT_EVIDENCE_BACKED", "detail": f"assertion claim {index} is not EvidenceBackedClaim"})
                continue
            if not claim.evidence.message_ids or not claim.evidence.messages:
                issues.append({"severity": "ERROR", "code": "A5_ASSERTION_EVIDENCE_MISSING", "detail": f"assertion claim {index} lacks enriched message evidence"})
            unknown = set(claim.evidence.message_ids) - {message.id for message in messages}
            if unknown:
                issues.append({"severity": "ERROR", "code": "A5_ASSERTION_EVIDENCE_OUTSIDE_CONTEXT", "detail": f"assertion claim {index} unknown IDs: {sorted(unknown)}"})

        for item in (*result.interpretations, *result.patterns):
            if item.evidence is None or tuple(snapshot.message_id for snapshot in item.evidence.messages) != expected_ids:
                issues.append({"severity": "ERROR", "code": "A5_STRUCTURED_CLAIM_EVIDENCE_MISSING", "detail": "interpretation/pattern lacks source-derived evidence snapshots"})

    bad_message_payload = dict(payload)
    bad_message_payload["summary"] = _claim("Invented claim", ["999"])
    try:
        parse_and_validate_result(bad_message_payload, context)
    except ValidationError:
        pass
    else:
        issues.append({"severity": "ERROR", "code": "A5_UNKNOWN_SUMMARY_EVIDENCE_ACCEPTED", "detail": "summary accepted message outside supplied AnalysisContext"})

    bad_metric_payload = dict(payload)
    bad_metric_payload["summary"] = _claim(
        "Invented metric claim",
        ["1"],
        metric_refs=[{"phase": "during", "name": "invented_metric"}],
    )
    try:
        parse_and_validate_result(bad_metric_payload, context)
    except ValidationError:
        pass
    else:
        issues.append({"severity": "ERROR", "code": "A5_UNKNOWN_SUMMARY_METRIC_ACCEPTED", "detail": "summary accepted deterministic metric outside AnalysisContext"})

    return {
        "schema_version": 2,
        "verdict": VERDICT_INVALID if issues else VERDICT_VALID,
        "structured_evidence_checks": "FAIL" if issues else "PASS",
        "assertion_surface": "evidence_backed" if not issues else "invalid",
        "issues": issues,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A7 audit of pinned A5 evidence-chain contract")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--expect", choices=[VERDICT_VALID, VERDICT_INVALID], default=VERDICT_VALID)
    args = parser.parse_args(argv)
    report = audit_a5_contract()
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["verdict"] == args.expect else 1


if __name__ == "__main__":
    raise SystemExit(main())
