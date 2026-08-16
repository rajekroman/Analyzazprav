from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone

from analyzazprav.a5_ai.integration_a6 import A6PacketError, messages_from_a6_packet
from analyzazprav.a5_ai.models import AnalysisContext, AnalysisMode, AnalysisType, MessageRecord
from analyzazprav.a5_ai.validator import ValidationError, parse_and_validate_result

from tools.a7_downstream.common import load_downstream_validator, write_report


def _context() -> AnalysisContext:
    first = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)
    second = datetime(2026, 8, 1, 8, 5, tzinfo=timezone.utc)
    return AnalysisContext(
        conversation_id="7",
        analysis_type=AnalysisType.SEGMENT,
        mode=AnalysisMode.RETROSPECTIVE,
        requested_start_ts=first,
        requested_end_ts=second,
        context_start_ts=first,
        context_end_ts=second,
        cutoff_ts=None,
        messages=(
            MessageRecord(id="m1", conversation_id="7", participant_id="p1", timestamp=first, text="  První   zpráva  "),
            MessageRecord(id="m2", conversation_id="7", participant_id="p2", timestamp=second, text="Druhá zpráva"),
        ),
        evidence_message_ids=("m1", "m2"),
        metrics_during={"conflict_score": 0.75},
        detected_signals=("manual_selection",),
    )


def _payload() -> dict:
    return {
        "summary": {
            "text": "Shrnutí vybraného úseku.",
            "confidence": 0.8,
            "evidence": {
                "message_ids": ["m1"],
                "description": "Přímá textová evidence.",
                "metric_refs": [{"phase": "during", "name": "conflict_score"}],
            },
        },
        "observations": [
            {
                "text": "Pozorovatelný fakt.",
                "strength": 0.9,
                "evidence": {"message_ids": ["m1"], "description": "", "metric_refs": []},
            }
        ],
        "interpretations": [
            {
                "text": "Možná interpretace.",
                "evidence_message_ids": ["m1", "m2"],
                "confidence": 0.6,
                "metric_refs": [],
            }
        ],
        "patterns": [
            {
                "pattern_type": "interaction",
                "description": "Opakující se vzorec.",
                "occurrences": 2,
                "confidence": 0.7,
                "evidence_message_ids": ["m1", "m2"],
                "metric_refs": [],
            }
        ],
        "turning_points": [
            {
                "text": "Bod obratu.",
                "confidence": 0.7,
                "evidence": {"message_ids": ["m2"], "description": "", "metric_refs": []},
            }
        ],
        "participant_p1": {
            "text": "Hypotéza k účastníkovi P1.",
            "confidence": 0.5,
            "evidence": {"message_ids": ["m1"], "description": "", "metric_refs": []},
        },
        "participant_p2": None,
        "shared_dynamic": {
            "text": "Možná sdílená dynamika.",
            "confidence": 0.5,
            "evidence": {"message_ids": ["m1", "m2"], "description": "", "metric_refs": []},
        },
        "alternative_explanations": ["Alternativní vysvětlení."],
        "unknowns": ["Motivaci nelze z dat spolehlivě určit."],
        "overall_confidence": 0.65,
    }


def _expect_validation_failure(context: AnalysisContext, payload: dict) -> bool:
    try:
        parse_and_validate_result(payload, context)
    except ValidationError:
        return True
    return False


def _duplicate_packet_rejected() -> bool:
    packet = {
        "schema_version": 1,
        "messages": [
            {"message_id": "m1", "conversation_id": "7", "sender": "p1", "timestamp": "2026-08-01T08:00:00+00:00", "text": "one"},
            {"message_id": "m1", "conversation_id": "7", "sender": "p2", "timestamp": "2026-08-01T08:01:00+00:00", "text": "two"},
        ],
    }
    try:
        messages_from_a6_packet(packet)
    except A6PacketError:
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    context = _context()
    payload = _payload()
    result = parse_and_validate_result(payload, context).to_dict()
    validator = load_downstream_validator()
    report = validator.validate_a5_result(context.prompt_payload(), result)
    report["contract_sha"] = "c1c5a0e9d5ec1933370054404bf3612f53f5a63e"

    bad_message = deepcopy(payload)
    bad_message["summary"]["evidence"]["message_ids"] = ["missing"]
    bad_metric = deepcopy(payload)
    bad_metric["summary"]["evidence"]["metric_refs"] = [{"phase": "during", "name": "invented_metric"}]
    negative_checks = {
        "unknown_summary_message_rejected": _expect_validation_failure(context, bad_message),
        "unknown_summary_metric_rejected": _expect_validation_failure(context, bad_metric),
        "duplicate_a6_packet_message_rejected": _duplicate_packet_rejected(),
    }
    report["checks"].update(negative_checks)
    failed = [name for name, ok in negative_checks.items() if not ok]
    if failed:
        report["issues"].append(
            {"severity": "ERROR", "code": "A5_FAIL_CLOSED_NEGATIVE_CHECK_FAILED", "detail": ", ".join(failed)}
        )
        report["status"] = "FAIL"
        report["verdict"] = "INVALID"

    write_report(report, args.report)
    return 0 if report["verdict"] == "VALID" else 1


if __name__ == "__main__":
    raise SystemExit(main())
