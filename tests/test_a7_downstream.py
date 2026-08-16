from __future__ import annotations

from copy import deepcopy

from analyzazprav.qa.downstream import (
    VERDICT_INVALID,
    VERDICT_NEEDS_REVIEW,
    VERDICT_VALID,
    aggregate_release_verdict,
    validate_a5_result,
    validate_a6_contract,
)


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


def test_a5_evidence_ref_rejects_extra_snapshot():
    result = deepcopy(_a5_result())
    result["summary_evidence"]["messages"].append(_evidence(("m2",), metric=False)["messages"][0])
    report = validate_a5_result(_a5_context(), result)
    assert report["verdict"] == VERDICT_INVALID
    assert any(issue["code"] == "A5_EVIDENCE_SNAPSHOT_EXTRA" for issue in report["issues"])


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
    report = aggregate_release_verdict({"core": valid, "A5": valid, "A6": valid})
    assert report["overall_verdict"] == VERDICT_VALID
    assert report["release_ready"] is True

    missing = aggregate_release_verdict({"core": valid, "A5": None, "A6": valid})
    assert missing["overall_verdict"] == VERDICT_NEEDS_REVIEW
    assert missing["release_ready"] is False

    invalid = aggregate_release_verdict(
        {"core": valid, "A5": valid, "A6": valid},
        job_results={"A6": "failure"},
    )
    assert invalid["overall_verdict"] == VERDICT_INVALID
    assert invalid["release_ready"] is False
