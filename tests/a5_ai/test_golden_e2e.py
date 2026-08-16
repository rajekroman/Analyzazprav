from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.a5_ai import (
    A2SQLiteMessageSource,
    A4SQLiteCandidateSource,
    AIAnalysisRequest,
    AIAnalyzer,
    AnalysisMode,
    AnalysisStatus,
    AnalysisType,
    ContextBuilder,
)
from analyzazprav.a5_ai.integration_a6 import candidate_from_a6_packet
from analyzazprav.a5_ai.providers import StaticProvider

UTC = timezone.utc


def us(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000)


class GoldenA4A5A6FlowTests(unittest.TestCase):
    def _build_database(self, path: Path) -> tuple[datetime, datetime]:
        first = datetime(2025, 5, 10, 12, 0, tzinfo=UTC)
        second = datetime(2025, 5, 10, 12, 2, tzinfo=UTC)
        third = datetime(2025, 5, 10, 12, 4, tzinfo=UTC)
        with sqlite3.connect(path) as conn:
            conn.executescript(
                """
                CREATE TABLE analysis_messages (
                    id INTEGER PRIMARY KEY,
                    conversation_id INTEGER NOT NULL,
                    sender_id INTEGER,
                    sent_at_utc_us INTEGER,
                    message_type TEXT,
                    text TEXT,
                    is_edited INTEGER NOT NULL DEFAULT 0,
                    is_deleted INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE analysis_attachments (
                    message_id INTEGER,
                    mime_type TEXT
                );
                CREATE TABLE message_relation (
                    id INTEGER PRIMARY KEY,
                    source_message_id INTEGER NOT NULL,
                    target_message_id INTEGER NOT NULL,
                    relation_type TEXT NOT NULL
                );
                CREATE TABLE a4_reconciliation_fixture (
                    conversation_id INTEGER,
                    membership_count_delta INTEGER,
                    invalid_response_session_count INTEGER,
                    invalid_silence_session_count INTEGER,
                    invalid_event_session_count INTEGER,
                    uses_latest_processing_run INTEGER,
                    reconciliation_ok INTEGER,
                    a4_source_membership_count INTEGER,
                    sender_accounted_membership_count INTEGER
                );
                CREATE VIEW analysis_a4_reconciliation AS
                    SELECT * FROM a4_reconciliation_fixture;
                CREATE TABLE a4_events_fixture (
                    id INTEGER PRIMARY KEY,
                    conversation_id INTEGER NOT NULL,
                    session_id INTEGER,
                    event_type TEXT NOT NULL,
                    score REAL NOT NULL,
                    start_at_utc_us INTEGER,
                    end_at_utc_us INTEGER,
                    factors_json TEXT NOT NULL,
                    source_message_ids_json TEXT NOT NULL
                );
                CREATE VIEW analysis_a4_events AS
                    SELECT * FROM a4_events_fixture;
                """
            )
            conn.executemany(
                "INSERT INTO analysis_messages VALUES (?, 7, ?, ?, 'text', ?, 0, 0)",
                [
                    (1, 11, us(first), "Can we talk about what happened?"),
                    (2, 22, us(second), "I am upset about this."),
                    (3, 11, us(third), "I want to understand and fix it."),
                ],
            )
            conn.execute("INSERT INTO message_relation VALUES (1, 3, 2, 'reply')")
            conn.execute(
                "INSERT INTO a4_reconciliation_fixture VALUES (7,0,0,0,0,1,1,3,3)"
            )
            conn.execute(
                "INSERT INTO a4_events_fixture VALUES (1, 7, 4, 'conflict', 0.82, ?, ?, ?, ?)",
                (
                    us(first),
                    us(third),
                    json.dumps({"negative": 0.7, "rapid_exchange": 0.5}),
                    json.dumps([1, 2, 3]),
                ),
            )
        return first, third

    @staticmethod
    def _provider_payload() -> dict:
        evidence = {
            "message_ids": ["1", "2", "3"],
            "metric_refs": [{"phase": "during", "name": "conflict_score"}],
            "description": "Conflict candidate and repair attempt are directly visible in supplied messages.",
        }
        return {
            "summary": {
                "text": "The selected exchange contains explicit upset followed by a repair-oriented response.",
                "confidence": 0.88,
                "evidence": evidence,
            },
            "observations": [
                {
                    "text": "Participant 22 explicitly states being upset.",
                    "evidence": {"message_ids": ["2"], "description": "Direct wording."},
                    "strength": 1.0,
                }
            ],
            "interpretations": [
                {
                    "text": "The final message is consistent with a repair attempt, without proving resolution.",
                    "evidence_message_ids": ["3"],
                    "confidence": 0.82,
                }
            ],
            "patterns": [],
            "turning_points": [
                {
                    "text": "The exchange shifts from stated upset to an explicit attempt to understand and repair.",
                    "confidence": 0.8,
                    "evidence": {"message_ids": ["2", "3"], "description": "Chronological shift."},
                }
            ],
            "participant_p1": None,
            "participant_p2": None,
            "shared_dynamic": {
                "text": "The evidence supports conflict plus attempted repair, but not confirmed resolution.",
                "confidence": 0.8,
                "evidence": evidence,
            },
            "alternative_explanations": [
                "The exchange may be a short isolated disagreement rather than a recurring pattern."
            ],
            "unknowns": ["The later outcome of the disagreement is outside blind-mode context."],
            "overall_confidence": 0.84,
        }

    def test_a4_sqlite_finding_to_a5_to_a6_compatible_evidence_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "golden.sqlite3"
            first, third = self._build_database(db)

            candidates = A4SQLiteCandidateSource(db).conflicts("7")
            self.assertEqual(len(candidates), 1)
            candidate = candidates[0]
            self.assertEqual(candidate.evidence_message_ids, ("1", "2", "3"))
            self.assertEqual(candidate.metrics_during["conflict_score"], 0.82)

            request = AIAnalysisRequest(
                conversation_id="7",
                analysis_type=AnalysisType.CONFLICT,
                start_ts=first,
                end_ts=third,
                mode=AnalysisMode.BLIND,
                candidate_id=candidate.id,
            )
            source = A2SQLiteMessageSource(db)
            execution = AIAnalyzer(
                context_builder=ContextBuilder(source),
                provider=StaticProvider(self._provider_payload()),
            ).analyze(request, candidate)

            self.assertEqual(execution.status, AnalysisStatus.COMPLETED)
            result = execution.result
            self.assertIsNotNone(result)
            self.assertEqual(result.summary_evidence.message_ids, ("1", "2", "3"))
            self.assertEqual(result.summary_evidence.metrics[0].value, 0.82)
            self.assertEqual(result.summary_evidence.messages[1].sender_id, "22")
            self.assertEqual(result.summary_evidence.messages[1].excerpt, "I am upset about this.")
            self.assertEqual(result.turning_point_evidence[0].message_ids, ("2", "3"))
            self.assertEqual(result.shared_dynamic_evidence.message_ids, ("1", "2", "3"))

            packet = {
                "schema_version": 1,
                "selected_message_ids": ["2", "3"],
                "selected_message_count": 2,
                "message_count": 3,
                "context_before": 1,
                "context_after": 0,
                "messages": [
                    {
                        "message_id": message.id,
                        "conversation_id": message.conversation_id,
                        "contact": "golden",
                        "sender": message.participant_id,
                        "timestamp": message.timestamp.isoformat(),
                        "text": message.text,
                        "selected": message.id in {"2", "3"},
                    }
                    for message in source.list_messages("7", first, third)
                ],
            }
            a6_candidate = candidate_from_a6_packet(packet)
            self.assertEqual(a6_candidate.evidence_message_ids, ("2", "3"))
            self.assertTrue(
                set(a6_candidate.evidence_message_ids).issubset(
                    set(result.summary_evidence.message_ids)
                )
            )


if __name__ == "__main__":
    unittest.main()
