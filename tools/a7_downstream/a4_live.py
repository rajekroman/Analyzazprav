from __future__ import annotations

import argparse
from dataclasses import asdict

from analyzazprav.analytics import AnalyticMessage, AnalyticsConfig, analyze_conversation

from tools.a7_downstream.common import load_downstream_validator, write_report


CONTRACT_SHA = "642c6413d304a802de69f43e65cad599dea78cd1"

SOURCE = [
    {"membership_id": 101, "message_id": 1, "conversation_id": 7, "participant_id": 10, "session_id": 1, "sequence_number": 1, "timestamp_us": 0, "word_count": 2, "text": "projekt alpha"},
    {"membership_id": 102, "message_id": 2, "conversation_id": 7, "participant_id": 10, "session_id": 1, "sequence_number": 2, "timestamp_us": 60_000_000, "word_count": 3, "text": "projekt alpha pokračuje"},
    {"membership_id": 103, "message_id": 3, "conversation_id": 7, "participant_id": 20, "session_id": 1, "sequence_number": 3, "timestamp_us": 300_000_000, "word_count": 4, "text": "projekt alpha další odpověď"},
    {"membership_id": 104, "message_id": 4, "conversation_id": 7, "participant_id": 10, "session_id": 1, "sequence_number": 4, "timestamp_us": 360_000_000, "word_count": 2, "text": "jiná odpověď"},
    {"membership_id": 105, "message_id": 5, "conversation_id": 7, "participant_id": 10, "session_id": 1, "sequence_number": 5, "timestamp_us": 420_000_000, "word_count": 1, "text": "konec"},
    {"membership_id": 106, "message_id": 6, "conversation_id": 7, "participant_id": 20, "session_id": 2, "sequence_number": 6, "timestamp_us": 3_600_000_000, "word_count": 2, "text": "projekt alpha"},
    {"membership_id": 107, "message_id": 7, "conversation_id": 7, "participant_id": None, "session_id": 3, "sequence_number": 7, "timestamp_us": None, "word_count": 1, "text": "unknown"},
]


def _message(row: dict) -> AnalyticMessage:
    text = str(row["text"])
    session_id = int(row["session_id"])
    return AnalyticMessage(
        membership_id=int(row["membership_id"]),
        message_id=int(row["message_id"]),
        conversation_id=int(row["conversation_id"]),
        participant_id=row["participant_id"],
        timestamp_us=row["timestamp_us"],
        text_clean=text,
        session_id=session_id,
        sequence_number=int(row["sequence_number"]),
        word_count=int(row["word_count"]),
        character_count=len(text),
        question_mark_count=text.count("?"),
        exclamation_mark_count=text.count("!"),
        utc_date="2026-01-01" if session_id == 1 else "2026-01-02" if session_id == 2 else None,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    result = analyze_conversation(
        [_message(row) for row in SOURCE],
        AnalyticsConfig(topic_min_document_frequency=2),
    )
    serialized = asdict(result)
    source = [
        {
            "membership_id": row["membership_id"],
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
    validator = load_downstream_validator()
    report = validator.validate_a4_result(source, serialized)
    report["contract_sha"] = CONTRACT_SHA
    report["checks"]["topic_candidate_count"] = len(serialized.get("topic_candidates") or [])
    report["checks"]["topic_evidence_count"] = len(serialized.get("topic_evidence") or [])
    if not serialized.get("topic_candidates") or not serialized.get("topic_evidence"):
        report["issues"].append(
            {
                "severity": "ERROR",
                "code": "A4_LIVE_TOPIC_EVIDENCE_MISSING",
                "detail": "Pinned live fixture must emit lexical topic candidate and evidence rows",
            }
        )
        report["status"] = "FAIL"
        report["verdict"] = "INVALID"
    write_report(report, args.report)
    return 0 if report["verdict"] == "VALID" else 1


if __name__ == "__main__":
    raise SystemExit(main())
