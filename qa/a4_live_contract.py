from __future__ import annotations

import json
from dataclasses import asdict

from analyzazprav.analytics import AnalyticMessage, AnalyticsConfig, analyze_conversation

from qa.analytics_validator import STATUS_PASS, validate_analytics_result


SOURCE = [
    {"message_id": 1, "conversation_id": 7, "participant_id": 10, "session_id": 1, "sequence_number": 1, "timestamp_us": 0, "word_count": 2, "text": "projekt alpha"},
    {"message_id": 2, "conversation_id": 7, "participant_id": 10, "session_id": 1, "sequence_number": 2, "timestamp_us": 60_000_000, "word_count": 3, "text": "projekt alpha pokračuje"},
    {"message_id": 3, "conversation_id": 7, "participant_id": 20, "session_id": 1, "sequence_number": 3, "timestamp_us": 300_000_000, "word_count": 4, "text": "projekt alpha další odpověď"},
    {"message_id": 4, "conversation_id": 7, "participant_id": 10, "session_id": 1, "sequence_number": 4, "timestamp_us": 360_000_000, "word_count": 2, "text": "jiná odpověď"},
    {"message_id": 5, "conversation_id": 7, "participant_id": 10, "session_id": 1, "sequence_number": 5, "timestamp_us": 420_000_000, "word_count": 1, "text": "konec"},
    {"message_id": 6, "conversation_id": 7, "participant_id": 20, "session_id": 2, "sequence_number": 6, "timestamp_us": 3_600_000_000, "word_count": 2, "text": "projekt alpha"},
    {"message_id": 7, "conversation_id": 7, "participant_id": None, "session_id": 3, "sequence_number": 7, "timestamp_us": None, "word_count": 1, "text": "unknown"},
]


def _message(row: dict) -> AnalyticMessage:
    text = str(row["text"])
    return AnalyticMessage(
        message_id=int(row["message_id"]),
        conversation_id=int(row["conversation_id"]),
        participant_id=row["participant_id"],
        timestamp_us=row["timestamp_us"],
        text_clean=text,
        session_id=int(row["session_id"]),
        sequence_number=int(row["sequence_number"]),
        word_count=int(row["word_count"]),
        character_count=len(text),
        question_mark_count=text.count("?"),
        exclamation_mark_count=text.count("!"),
        utc_date="2026-01-01" if int(row["session_id"]) == 1 else "2026-01-02" if int(row["session_id"]) == 2 else None,
    )


def main() -> int:
    config = AnalyticsConfig(topic_min_document_frequency=2)
    result = analyze_conversation([_message(row) for row in SOURCE], config)
    serialized = asdict(result)
    oracle_source = [
        {
            "message_id": row["message_id"],
            "conversation_id": row["conversation_id"],
            "participant_id": row["participant_id"],
            "session_id": row["session_id"],
            "sequence_number": row["sequence_number"],
            "timestamp_us": row["timestamp_us"],
            "word_count": row["word_count"],
        }
        for row in SOURCE
    ]
    report = validate_analytics_result(oracle_source, serialized)
    issues = list(report["issues"])
    source_ids = {int(row["message_id"]) for row in SOURCE}

    candidates = serialized.get("topic_candidates", [])
    evidence = serialized.get("topic_evidence", [])
    candidate_keys = {str(row["topic_key"]) for row in candidates}
    for index, row in enumerate(candidates):
        ids = {int(value) for value in row.get("source_message_ids", [])}
        unknown = sorted(ids - source_ids)
        if unknown:
            issues.append({
                "severity": "ERROR",
                "code": "A4_TOPIC_CANDIDATE_EVIDENCE_NOT_IN_SOURCE",
                "detail": f"topic_candidates[{index}] unknown IDs: {unknown}",
            })
    for index, row in enumerate(evidence):
        message_id = int(row["message_id"])
        topic_key = str(row["topic_key"])
        if message_id not in source_ids:
            issues.append({
                "severity": "ERROR",
                "code": "A4_TOPIC_EVIDENCE_NOT_IN_SOURCE",
                "detail": f"topic_evidence[{index}] message_id={message_id}",
            })
        if topic_key not in candidate_keys:
            issues.append({
                "severity": "ERROR",
                "code": "A4_TOPIC_EVIDENCE_WITHOUT_CANDIDATE",
                "detail": f"topic_evidence[{index}] topic_key={topic_key!r}",
            })

    if not candidates or not evidence:
        issues.append({
            "severity": "ERROR",
            "code": "A4_TOPIC_GOLDEN_EVIDENCE_MISSING",
            "detail": "Pinned A4 golden dataset must produce lexical topic candidate and evidence rows",
        })

    output = {
        "status": "FAIL" if issues else STATUS_PASS,
        "oracle_status": report["status"],
        "source_message_count": serialized.get("source_message_count"),
        "turn_count": serialized.get("turn_count"),
        "session_count": serialized.get("session_count"),
        "topic_candidate_count": len(candidates),
        "topic_evidence_count": len(evidence),
        "issues": issues,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if issues or report["status"] != STATUS_PASS else 0


if __name__ == "__main__":
    raise SystemExit(main())
