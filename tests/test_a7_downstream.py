from __future__ import annotations

from copy import deepcopy

from analyzazprav.qa.downstream import (
    VERDICT_INVALID,
    VERDICT_NEEDS_REVIEW,
    VERDICT_VALID,
    aggregate_release_verdict,
    validate_a4_result,
    validate_a5_result,
    validate_a6_contract,
)


def _a4_source():
    return [
        {"membership_id": 101, "message_id": 1, "conversation_id": 7, "participant_id": 10, "session_id": 1, "sequence_number": 1, "timestamp_us": 0, "word_count": 2},
        {"membership_id": 102, "message_id": 2, "conversation_id": 7, "participant_id": 10, "session_id": 1, "sequence_number": 2, "timestamp_us": 60_000_000, "word_count": 1},
        {"membership_id": 103, "message_id": 3, "conversation_id": 7, "participant_id": 20, "session_id": 1, "sequence_number": 3, "timestamp_us": 300_000_000, "word_count": 4},
        {"membership_id": 104, "message_id": 4, "conversation_id": 7, "participant_id": 20, "session_id": 2, "sequence_number": 4, "timestamp_us": 14_400_000_000, "word_count": 2},
    ]


def _a4_result():
    return {
        "conversation_id": 7,
        "source_message_count": 4,
        "known_sender_message_count": 4,
        "unknown_sender_message_count": 0,
        "turn_count": 3,
        "session_count": 2,
        "turns": [
            {"turn_id": 1, "conversation_id": 7, "session_id": 1, "participant_id": 10, "start_us": 0, "end_us": 60_000_000, "message_ids": [1, 2], "message_count": 2, "word_count": 3},
            {"turn_id": 2, "conversation_id": 7, "session_id": 1, "participant_id": 20, "start_us": 300_000_000, "end_us": 300_000_000, "message_ids": [3], "message_count": 1, "word_count": 4},
            {"turn_id": 3, "conversation_id": 7, "session_id": 2, "participant_id": 20, "start_us": 14_400_000_000, "end_us": 14_400_000_000, "message_ids": [4], "message_count": 1, "word_count": 2},
        ],
        "response_samples": [
            {"conversation_id": 7, "session_id": 1, "from_participant_id": 10, "responder_id": 20, "previous_turn_id": 1, "response_turn_id": 2, "latency_seconds": 240.0, "response_effort_ratio": 4 / 3},
        ],
        "participant_metrics": {
            10: {
                "message_count": 2,
                "word_count": 3,
                "turn_count": 1,
                "initiations": 1,
                "initiation_share": 0.5,
                "response_turn_count": 0,
                "latency_sample_count": 0,
                "unanswered_turn_count": 0,
                "mean_response_latency_seconds": None,
                "median_response_latency_seconds": None,
                "p25_response_latency_seconds": None,
                "p75_response_latency_seconds": None,
                "p90_response_latency_seconds": None,
                "median_response_effort_ratio": None,
            },
            20: {
                "message_count": 2,
                "word_count": 6,
                "turn_count": 2,
                "initiations": 1,
                "initiation_share": 0.5,
                "response_turn_count": 1,
                "latency_sample_count": 1,
                "unanswered_turn_count": 2,
                "mean_response_latency_seconds": 240.0,
                "median_response_latency_seconds": 240.0,
                "p25_response_latency_seconds": 240.0,
                "p75_response_latency_seconds": 240.0,
                "p90_response_latency_seconds": 240.0,
                "median_response_effort_ratio": 4 / 3,
            },
        },
        "reciprocity": {
            "message_reciprocity": 1.0,
            "word_reciprocity": 0.5,
            "turn_reciprocity": 0.5,
            "initiation_reciprocity": 1.0,
        },
        "conflicts": [{"source_message_ids": [1, 3]}],
        "silence_events": [],
        "time_buckets": [],
        "daily_metrics": [],
        "change_points": [],
        "period_metrics": [],
        "engagement_signals": [],
        "dyadic_regimes": [],
        "trend_summaries": [],
        "topic_candidates": [{"topic_key": "projekt", "source_message_ids": [1, 3]}],
        "topic_evidence": [{"topic_key": "projekt", "message_id": 1}],
    }


def test_a4_independent_oracle_accepts_exact_result():
    report = validate_a4_result(_a4_source(), _a4_result())
    assert report["verdict"] == VERDICT_VALID, report


def test_a4_independent_oracle_rejects_wrong_latency():
    result = deepcopy(_a4_result())
    result["response_samples"][0]["latency_seconds"] = 239.0
    report = validate_a4_result(_a4_source(), result)
    assert report["verdict"] == VERDICT_INVALID
    assert any(issue["code"] == "A4_RESPONSE_VALUE_MISMATCH" for issue in report["issues"])


def test_a4_independent_oracle_rejects_evidence_outside_source():
    result = deepcopy(_a4_result())
    result["conflicts"][0]["source_message_ids"] = [1, 999]
    report = validate_a4_result(_a4_source(), result)
    assert report["verdict"] == VERDICT_INVALID
    assert any(issue["code"] == "A4_EVIDENCE_OUTSIDE_SOURCE" for issue in report["issues"])


def _a5_context():
    return {
        "messages": [
            {"id": "m1", "timestamp": "2026-08-01T08:00:00+00:00", "participant_id": "p1", "text": "  První   zpráva  "},
            {"id": "m2", "timestamp": "2026-08-01T08:05:00+00:00", "participant_id": "p2", "text": "Druhá zpráva"},
        ],
        "metrics": {"before": {}, "during": {"conflict_score": 0.75}, "after": {}},
    }


def _evidence(ids=("m1",), *, metric=True):
    rows = {
        "m1": {"message_id": "m1", "timestamp": "2026-08-01T08:00:00+00:00", "sender_id": "p1", "excerpt": "První zpráva"},
        "m2": {"message_id": "m2", "timestamp": "2026-08-01T08:05:00+00:00", "sender_id": "p2", "excerpt": "Druhá zpráva"},
    }
    return {
        "message_ids": list(ids),
        "messages": [rows[value] for value in ids if value in rows],
        "metrics": ([{"phase": "during", "name": "conflict_score", "value": 0.75}] if metric else []),
    }


def _a5_result():
    return {
        "summary": "Shrnutí",
        "summary_evidence": _evidence(("m1",)),
        "observations": [{"text": "Pozorování", "strength": 0.8, "evidence": _evidence(("m1",), metric=False)}],
        "interpretations": [{"text": "Interpretace", "confidence": 0.6, "evidence_message_ids": ["m1"], "evidence": _evidence(("m1",), metric=False)}],
        "patterns": [{"pattern_type": "cycle", "description": "Vzorec", "confidence": 0.7, "evidence_message_ids": ["m1", "m2"], "evidence": _evidence(("m1", "m2"), metric=False)}],
        "turning_points": ["Bod obratu"],
        "turning_point_evidence": [_evidence(("m2",), metric=False)],
        "participant_p1": "Hypotéza P1",
        "participant_p1_evidence": _evidence(("m1",), metric=False),
        "participant_p2": None,
        "participant_p2_evidence": None,
        "shared_dynamic": "Sdílená dynamika",
        "shared_dynamic_evidence": _evidence(("m1", "m2"), metric=False),
    }


def test_a5_evidence_chain_accepts_source_derived_snapshots():
    report = validate_a5_result(_a5_context(), _a5_result())
    assert report["verdict"] == VERDICT_VALID, report


def test_a5_evidence_chain_rejects_wrong_snapshot_and_metric():
    result = deepcopy(_a5_result())
    result["summary_evidence"]["messages"][0]["sender_id"] = "invented"
    result["summary_evidence"]["metrics"][0]["value"] = 0.1
    report = validate_a5_result(_a5_context(), result)
    assert report["verdict"] == VERDICT_INVALID
    codes = {issue["code"] for issue in report["issues"]}
    assert "A5_EVIDENCE_SNAPSHOT_MISMATCH" in codes
    assert "A5_METRIC_EVIDENCE_VALUE_MISMATCH" in codes


def test_a5_assertion_without_evidence_is_invalid():
    result = deepcopy(_a5_result())
    result["shared_dynamic_evidence"] = None
    report = validate_a5_result(_a5_context(), result)
    assert report["verdict"] == VERDICT_INVALID
    assert any(issue["code"] == "A5_ASSERTION_EVIDENCE_MISSING" for issue in report["issues"])


RENDERER_SOURCE = '''
def render_result_evidence(ids, frame, db):
    return ids

def render_evidence_ref(evidence, frame, db):
    render_result_evidence(evidence.get("message_ids", []), frame, db)

def render_assertion(title, claim, parallel_evidence, frame, db):
    evidence = claim.get("evidence") if isinstance(claim, dict) else parallel_evidence
    render_evidence_ref(evidence, frame, db)

def render_a5_execution(execution, frame, db):
    result = execution.get("result") or {}
    render_assertion("Summary", result.get("summary"), result.get("summary_evidence"), frame, db)
    render_assertion("P1", result.get("participant_p1"), result.get("participant_p1_evidence"), frame, db)
    render_assertion("P2", result.get("participant_p2"), result.get("participant_p2_evidence"), frame, db)
    render_assertion("Shared", result.get("shared_dynamic"), result.get("shared_dynamic_evidence"), frame, db)
    turning_points = result.get("turning_points") or []
    turning_point_evidence = result.get("turning_point_evidence") or []
    for index, claim in enumerate(turning_points):
        render_assertion("Turning", claim, turning_point_evidence[index], frame, db)
'''


def _a6_kwargs():
    expected = [
        {"membership_id": "11", "message_id": "1", "conversation_id": "c1", "timestamp_known": True},
        {"membership_id": "12", "message_id": "1", "conversation_id": "c2", "timestamp_known": True},
        {"membership_id": "13", "message_id": "2", "conversation_id": "c1", "timestamp_known": False},
    ]
    packet = {
        "selected_message_ids": ["1"],
        "messages": [
            {"membership_id": "11", "message_id": "1", "conversation_id": "c1", "selected": True},
        ],
    }
    message_sources = [{"message_id": "1", "source_record_key": "source-1"}]
    attachments = [{"occurrence_id": "21", "message_id": "1", "attachment_id": "31"}]
    attachment_sources = [{"attachment_source_id": "41", "occurrence_id": "21", "message_id": "1", "source_occurrence_key": "att-1"}]
    return {
        "expected_memberships": expected,
        "actual_rows": deepcopy(expected),
        "packet": packet,
        "requested_selected_ids": ["1"],
        "expected_message_sources": message_sources,
        "actual_message_sources": deepcopy(message_sources),
        "expected_attachments": attachments,
        "actual_attachments": deepcopy(attachments),
        "expected_attachment_sources": attachment_sources,
        "actual_attachment_sources": deepcopy(attachment_sources),
        "renderer_source": RENDERER_SOURCE,
    }


def test_a6_contract_accepts_lossless_membership_and_provenance_projection():
    report = validate_a6_contract(**_a6_kwargs())
    assert report["verdict"] == VERDICT_VALID, report


def test_a6_contract_rejects_membership_loss_and_timestamp_rewrite():
    kwargs = _a6_kwargs()
    kwargs["actual_rows"] = kwargs["actual_rows"][:-1]
    kwargs["actual_rows"][0]["timestamp_known"] = False
    report = validate_a6_contract(**kwargs)
    assert report["verdict"] == VERDICT_INVALID
    codes = {issue["code"] for issue in report["issues"]}
    assert "A6_MEMBERSHIP_SET_MISMATCH" in codes
    assert "A6_TIMESTAMP_PRESERVATION_MISMATCH" in codes


def test_a6_contract_rejects_attachment_provenance_rewrite():
    kwargs = _a6_kwargs()
    kwargs["actual_attachment_sources"][0]["source_occurrence_key"] = "rewritten"
    report = validate_a6_contract(**kwargs)
    assert report["verdict"] == VERDICT_INVALID
    assert any(issue["code"] == "A6_PROVENANCE_PROJECTION_MISMATCH" for issue in report["issues"])


def test_release_aggregate_requires_every_component_valid():
    valid = {"verdict": VERDICT_VALID}
    report = aggregate_release_verdict({"core": valid, "A4": valid, "A5": valid, "A6": valid})
    assert report["overall_verdict"] == VERDICT_VALID
    assert report["release_ready"] is True

    missing = aggregate_release_verdict({"core": valid, "A4": None})
    assert missing["overall_verdict"] == VERDICT_NEEDS_REVIEW
    assert missing["release_ready"] is False

    invalid = aggregate_release_verdict(
        {"core": valid, "A4": valid},
        job_results={"A4": "failure"},
    )
    assert invalid["overall_verdict"] == VERDICT_INVALID
    assert invalid["release_ready"] is False
