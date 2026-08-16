from __future__ import annotations

import argparse
import json
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path

from analyzazprav.a5_ai.integration_a6 import A6PacketError, messages_from_a6_packet
from analyzazprav.a5_ai.models import (
    AIAnalysisResult,
    AnalysisContext,
    AnalysisMode,
    AnalysisType,
    MessageRecord,
)
from analyzazprav.a5_ai.validator import ValidationError, parse_and_validate_result

VERDICT_VALID = "VALID"
VERDICT_PARTIAL = "PARTIALLY_VALID"
VERDICT_INVALID = "INVALID"


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
    payload = {
        "summary": "The interaction contains a response.",
        "observations": [
            {
                "text": "A reply follows the first message.",
                "evidence": {
                    "message_ids": ["1", "2"],
                    "description": "Two-message sequence",
                    "metric_refs": [
                        {"phase": "during", "name": "median_response_latency_seconds"}
                    ],
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
        "turning_points": [],
        "participant_p1": None,
        "participant_p2": None,
        "shared_dynamic": None,
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
            issues.append({
                "severity": "ERROR",
                "code": "A5_MESSAGE_EVIDENCE_ID_MISMATCH",
                "detail": "validated evidence snapshots do not preserve source message IDs",
            })
        if evidence.messages[0].timestamp != messages[0].timestamp.isoformat():
            issues.append({
                "severity": "ERROR",
                "code": "A5_EVIDENCE_TIMESTAMP_MISMATCH",
                "detail": "evidence timestamp was not derived exactly from AnalysisContext",
            })
        if evidence.messages[0].sender_id != "p1":
            issues.append({
                "severity": "ERROR",
                "code": "A5_EVIDENCE_SENDER_MISMATCH",
                "detail": "evidence sender was not derived exactly from AnalysisContext",
            })
        if evidence.messages[0].excerpt != "Evidence text with whitespace":
            issues.append({
                "severity": "ERROR",
                "code": "A5_EVIDENCE_EXCERPT_MISMATCH",
                "detail": f"unexpected normalized excerpt: {evidence.messages[0].excerpt!r}",
            })
        if len(evidence.metrics) != 1 or evidence.metrics[0].value != 60.0:
            issues.append({
                "severity": "ERROR",
                "code": "A5_METRIC_EVIDENCE_VALUE_MISMATCH",
                "detail": "metric evidence was not enriched from deterministic context value",
            })
        for item in (*result.interpretations, *result.patterns):
            ref = item.evidence
            if ref is None or tuple(snapshot.message_id for snapshot in ref.messages) != expected_ids:
                issues.append({
                    "severity": "ERROR",
                    "code": "A5_STRUCTURED_CLAIM_EVIDENCE_MISSING",
                    "detail": "interpretation/pattern lacks source-derived evidence snapshots",
                })

    invalid_payload = dict(payload)
    invalid_payload["observations"] = [
        {"text": "bad", "evidence": {"message_ids": ["999"]}, "strength": 0.5}
    ]
    try:
        parse_and_validate_result(invalid_payload, context)
    except ValidationError:
        pass
    else:
        issues.append({
            "severity": "ERROR",
            "code": "A5_UNKNOWN_MESSAGE_EVIDENCE_ACCEPTED",
            "detail": "A5 accepted model evidence outside supplied AnalysisContext",
        })

    invalid_metric_payload = dict(payload)
    invalid_metric_payload["observations"] = [
        {
            "text": "bad metric",
            "evidence": {
                "message_ids": ["1"],
                "metric_refs": [{"phase": "during", "name": "invented_metric"}],
            },
            "strength": 0.5,
        }
    ]
    try:
        parse_and_validate_result(invalid_metric_payload, context)
    except ValidationError:
        pass
    else:
        issues.append({
            "severity": "ERROR",
            "code": "A5_UNKNOWN_METRIC_EVIDENCE_ACCEPTED",
            "detail": "A5 accepted metric evidence outside deterministic AnalysisContext",
        })

    field_names = {field.name for field in fields(AIAnalysisResult)}
    summary_evidence_fields = {
        name for name in field_names if name.startswith("summary_") and "evidence" in name
    }
    partial_issues: list[dict[str, str]] = []
    if not summary_evidence_fields:
        partial_issues.append({
            "severity": "WARNING",
            "code": "A5_SUMMARY_EVIDENCE_CONTRACT_MISSING",
            "detail": "AIAnalysisResult.summary is free text without a direct message/metric evidence field",
        })

    if issues:
        verdict = VERDICT_INVALID
    elif partial_issues:
        verdict = VERDICT_PARTIAL
    else:
        verdict = VERDICT_VALID
    return {
        "schema_version": 1,
        "verdict": verdict,
        "structured_evidence_checks": "PASS" if not issues else "FAIL",
        "summary_evidence_fields": sorted(summary_evidence_fields),
        "issues": [*issues, *partial_issues],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A7 audit of pinned A5 evidence-chain contract")
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--expect",
        choices=[VERDICT_VALID, VERDICT_PARTIAL, VERDICT_INVALID],
        default=VERDICT_VALID,
    )
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
