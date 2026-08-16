from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.a5_ai import AnalysisMode, AnalysisType
from analyzazprav.a5_ai.integration_a2 import A2SQLiteMessageSource
from analyzazprav.a5_ai.integration_a6 import (
    A6PacketError,
    A6PacketMessageSource,
    candidate_from_a6_packet,
    request_from_a6_packet,
)

UTC = timezone.utc
BASE = datetime(2025, 5, 10, 12, 0, tzinfo=UTC)


def utc_us(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000)


class A2IntegrationTests(unittest.TestCase):
    def test_read_only_a2_source_preserves_reply_attachment_and_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "a2.sqlite3"
            with sqlite3.connect(db) as conn:
                conn.executescript("""
                    CREATE TABLE analysis_messages (
                        id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL,
                        sender_id INTEGER, sent_at_utc_us INTEGER, message_type TEXT,
                        text TEXT, is_edited INTEGER, is_deleted INTEGER
                    );
                    CREATE TABLE analysis_attachments (
                        message_id INTEGER, attachment_id INTEGER, sha256 TEXT,
                        mime_type TEXT, size_bytes INTEGER, filename TEXT,
                        storage_path TEXT, availability TEXT, position INTEGER
                    );
                    CREATE TABLE message_relation (
                        id INTEGER PRIMARY KEY, source_message_id INTEGER NOT NULL,
                        target_message_id INTEGER NOT NULL, relation_type TEXT NOT NULL
                    );
                """)
                conn.execute("INSERT INTO analysis_messages VALUES (1, 7, 11, ?, 'text', 'first', 0, 0)", (utc_us(BASE),))
                conn.execute("INSERT INTO analysis_messages VALUES (2, 7, 22, ?, 'image', 'second', 1, 0)", (utc_us(BASE + timedelta(minutes=1)),))
                conn.execute("INSERT INTO analysis_attachments VALUES (2, 10, NULL, 'image/jpeg', 123, 'x.jpg', NULL, 'available', 0)")
                conn.execute("INSERT INTO message_relation VALUES (1, 2, 1, 'reply')")
            rows = A2SQLiteMessageSource(db).list_messages("7", BASE - timedelta(seconds=1), BASE + timedelta(minutes=2))
            self.assertEqual([m.id for m in rows], ["1", "2"])
            self.assertEqual(rows[1].reply_to_message_id, "1")
            self.assertEqual(rows[1].attachment_types, ("image/jpeg",))
            self.assertTrue(rows[1].edited)
            self.assertEqual(rows[1].participant_id, "22")


class A6IntegrationTests(unittest.TestCase):
    def packet(self):
        return {
            "schema_version": 1,
            "selected_message_ids": ["m2"],
            "selected_message_count": 1,
            "message_count": 3,
            "context_before": 1,
            "context_after": 1,
            "messages": [
                {"message_id": "m1", "conversation_id": "c1", "contact": "X", "sender": "p1", "timestamp": BASE.isoformat(), "text": "before", "selected": False},
                {"message_id": "m2", "conversation_id": "c1", "contact": "X", "sender": "p2", "timestamp": (BASE + timedelta(minutes=1)).isoformat(), "text": "selected", "selected": True},
                {"message_id": "m3", "conversation_id": "c1", "contact": "X", "sender": "p1", "timestamp": (BASE + timedelta(minutes=2)).isoformat(), "text": "after", "selected": False},
            ],
        }

    def test_packet_builds_manual_candidate_with_selected_evidence(self):
        candidate = candidate_from_a6_packet(self.packet())
        self.assertTrue(candidate.manual_request)
        self.assertEqual(candidate.evidence_message_ids, ("m2",))
        self.assertEqual(candidate.start_ts, BASE + timedelta(minutes=1))
        self.assertEqual(candidate.conversation_id, "c1")

    def test_packet_builds_request_and_message_source(self):
        packet = self.packet()
        request = request_from_a6_packet(packet, analysis_type=AnalysisType.CONFLICT, mode=AnalysisMode.RETROSPECTIVE, user_question="What changed?")
        selected = A6PacketMessageSource.from_packet(packet).list_messages("c1", BASE, BASE + timedelta(minutes=2))
        self.assertEqual([m.id for m in selected], ["m1", "m2", "m3"])
        self.assertEqual(request.analysis_type, AnalysisType.CONFLICT)
        self.assertEqual(request.mode, AnalysisMode.RETROSPECTIVE)
        self.assertEqual(request.user_question, "What changed?")

    def test_duplicate_packet_message_id_fails_closed(self):
        packet = self.packet()
        packet["messages"].append(dict(packet["messages"][1]))
        with self.assertRaises(A6PacketError):
            A6PacketMessageSource.from_packet(packet)

    def test_duplicate_selected_message_id_fails_closed(self):
        packet = self.packet()
        packet["selected_message_ids"] = ["m2", "m2"]
        with self.assertRaises(A6PacketError):
            candidate_from_a6_packet(packet)


if __name__ == "__main__": unittest.main()
