from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from analyzazprav.a5_ai import AnalysisContext, AnalysisMode, AnalysisType, MessageRecord
from analyzazprav.a5_ai.validator import parse_and_validate_result
from analyzazprav.qa import validate_a5_evidence_chain
from tools.a7_release.common import finalize, issue, write_report

UTC = timezone.utc
BASE = datetime(2026, 8, 16, 8, 0, tzinfo=UTC)


def _context() -> AnalysisContext:
    message = MessageRecord(
        id="m1",
        membership_id="mem1",
        conversation_id="c1",
        participant_id="p1",
        timestamp=BASE + timedelta(minutes=1),
        text="source message",
        source_record_keys=("source-1",),
        source_snapshot_keys=("snapshot-1",),
        source_parser_versions=("parser-v1",),
    )
    return AnalysisContext(
        conversation_id="c1",
        analysis_type=AnalysisType.SEGMENT,
        mode=AnalysisMode.RETROSPECTIVE,
        requested_start_ts=BASE,
        requested_end_ts=BASE + timedelta(minutes=2),
        context_start_ts=BASE,
        context_end_ts=BASE + timedelta(minutes=2),
        cutoff_ts=None,
        messages=(message,),
        evidence_message_ids=("m1",),
        metrics_during={"median_response_latency_seconds": 60.0},
        candidate_provenance={
            "analytics_run_id": 17,
            "analytics_version": "9",
            "processing_run_id": 11,
            "analysis_signature": "sig-v9",
            "source_fingerprint": "source-fingerprint",
        },
        available_message_count=1,
    )


def _payload() -> dict:
    return {
        "summary": {
            "text": "Evidence-backed summary",
            "confidence": 0.8,
            "evidence": {
                "message_ids": ["m1"],
                "description": "source evidence",
                "metric_refs": [{"phase": "during", "name": "median_response_latency_seconds"}],
            },
        },
        "observations": [],
        "interpretations": [],
        "patterns": [],
        "turning_points": [],
        "participant_p1": None,
        "participant_p2": None,
        "shared_dynamic": None,
        "alternative_explanations": ["Other explanations remain possible."],
        "unknowns": ["Intent is not directly observable."],
        "overall_confidence": 0.8,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--contract-sha", required=True)
    args = parser.parse_args()

    checks: dict[str, object] = {}
    issues: list[dict[str, str]] = []
    context = _context()
    result = parse_and_validate_result(_payload(), context)
    oracle = validate_a5_evidence_chain(context, result)
    checks["a5_oracle_status"] = oracle["status"]
    checks["materialized_message_snapshots"] = oracle["checks"].get("message_evidence_snapshots")
    checks["materialized_metric_snapshots"] = oracle["checks"].get("metric_evidence_snapshots")
    if oracle["status"] != "PASS":
        issues.append(issue("ERROR", "A7_A5_CURRENT_ORACLE_FAILED", str(oracle.get("issues"))))

    evidence = result.summary_evidence
    snapshot = evidence.messages[0]
    checks.update({
        "membership_preserved": snapshot.membership_id == "mem1",
        "source_record_preserved": snapshot.source_record_keys == ("source-1",),
        "source_snapshot_preserved": snapshot.source_snapshot_keys == ("snapshot-1",),
        "source_parser_preserved": snapshot.source_parser_versions == ("parser-v1",),
        "metric_run_preserved": evidence.metrics[0].analytics_run_id == "17",
        "metric_version_preserved": evidence.metrics[0].analytics_version == "9",
        "metric_signature_preserved": evidence.metrics[0].analysis_signature == "sig-v9",
    })
    failed_exact = [name for name, value in checks.items() if name.endswith("_preserved") and value is not True]
    if failed_exact:
        issues.append(issue("ERROR", "A7_A5_PROVENANCE_PRESERVATION_FAILED", ", ".join(failed_exact)))

    corrupted_message = replace(snapshot, source_record_keys=())
    corrupted_evidence = replace(evidence, messages=(corrupted_message,))
    corrupted_result = replace(result, summary_evidence=corrupted_evidence)
    corrupted_report = validate_a5_evidence_chain(context, corrupted_result)
    detected_codes = {row["code"] for row in corrupted_report.get("issues", [])}
    checks["negative_source_provenance_corruption_rejected"] = (
        corrupted_report["status"] == "FAIL"
        and "A5_MESSAGE_PROVENANCE_MISMATCH" in detected_codes
        and "A5_SOURCE_RECORD_PROVENANCE_DROPPED" in detected_codes
    )
    if not checks["negative_source_provenance_corruption_rejected"]:
        issues.append(issue(
            "ERROR",
            "A7_A5_NEGATIVE_PROBE_FAILED",
            "Independent A5 oracle did not reject deliberately dropped source-record provenance.",
        ))

    report = finalize("A5", checks, issues, contract_sha=args.contract_sha)
    write_report(report, args.report)
    return 0 if report["verdict"] == "VALID" else 1


if __name__ == "__main__":
    raise SystemExit(main())
