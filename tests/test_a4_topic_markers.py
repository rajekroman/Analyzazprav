from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from analyzazprav.analytics import (
    AnalyticMessage,
    AnalyticsConfig,
    AnalyticsStore,
    analyze_conversation,
    analyze_database,
)


def message(message_id: int, text: str, participant_id: int) -> AnalyticMessage:
    return AnalyticMessage(
        message_id=message_id,
        conversation_id=10,
        participant_id=participant_id,
        timestamp_us=message_id * 1_000_000,
        text_clean=text,
        session_id=1,
        sequence_number=message_id,
        word_count=len(text.split()),
        character_count=len(text),
        question_mark_count=text.count("?"),
        exclamation_mark_count=text.count("!"),
        local_date=f"2026-06-0{message_id}",
        local_weekday=message_id - 1,
        local_hour=12,
    )


def create_contract_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE participant(id INTEGER PRIMARY KEY);
        CREATE TABLE conversation(id INTEGER PRIMARY KEY);
        CREATE TABLE message(id INTEGER PRIMARY KEY);
        CREATE TABLE processing_run(id INTEGER PRIMARY KEY, status TEXT NOT NULL);
        CREATE TABLE conversation_session(
            id INTEGER PRIMARY KEY,
            conversation_id INTEGER NOT NULL REFERENCES conversation(id)
        );
        CREATE TABLE analysis_messages(
            id INTEGER PRIMARY KEY,
            conversation_id INTEGER NOT NULL,
            sender_id INTEGER,
            sent_at_utc_us INTEGER
        );
        CREATE TABLE processed_message(
            message_id INTEGER PRIMARY KEY,
            text_clean TEXT,
            session_id INTEGER NOT NULL,
            sequence_number INTEGER NOT NULL,
            word_count INTEGER NOT NULL,
            char_count INTEGER NOT NULL,
            question_mark_count INTEGER NOT NULL,
            exclamation_mark_count INTEGER NOT NULL,
            has_attachment INTEGER NOT NULL,
            utc_year INTEGER,
            utc_month INTEGER,
            utc_day INTEGER,
            utc_weekday INTEGER,
            utc_hour INTEGER,
            local_year INTEGER,
            local_month INTEGER,
            local_day INTEGER,
            local_weekday INTEGER,
            local_hour INTEGER
        );
        INSERT INTO participant VALUES (1), (2);
        INSERT INTO conversation VALUES (10);
        INSERT INTO message VALUES (1), (2), (3);
        INSERT INTO processing_run VALUES (1, 'completed');
        INSERT INTO conversation_session VALUES (1, 10);
        INSERT INTO analysis_messages VALUES
            (1, 10, 1, 1000000),
            (2, 10, 2, 2000000),
            (3, 10, 1, 3000000);
        INSERT INTO processed_message VALUES
            (1, 'Dovolená Chorvatsko ❤️', 1, 1, 3, 23, 0, 0, 0,
             2026, 6, 1, 0, 10, 2026, 6, 1, 0, 12),
            (2, 'Dovolená Chorvatsko vadí mi', 1, 2, 4, 29, 0, 0, 0,
             2026, 6, 2, 1, 10, 2026, 6, 2, 1, 12),
            (3, 'Dovolená Chorvatsko hotel', 1, 3, 3, 25, 0, 0, 0,
             2026, 6, 3, 2, 10, 2026, 6, 3, 2, 12);
        """
    )
    return conn


class TopicMarkerAnalyticsTests(unittest.TestCase):
    def test_topic_marker_evidence_is_sparse_deterministic_and_traceable(self) -> None:
        source = [
            message(1, "Dovolená Chorvatsko ❤️", 1),
            message(2, "Dovolená Chorvatsko vadí mi", 2),
            message(3, "Dovolená Chorvatsko hotel", 1),
        ]
        config = AnalyticsConfig(topic_min_document_frequency=2)
        forward = analyze_conversation(source, config)
        reverse = analyze_conversation(list(reversed(source)), config)

        topic_key = "lexical_ngram_v1:2:dovolená chorvatsko"
        forward_rows = [
            row for row in forward.topic_marker_evidence if row.topic_key == topic_key
        ]
        reverse_rows = [
            row for row in reverse.topic_marker_evidence if row.topic_key == topic_key
        ]
        self.assertEqual(forward_rows, reverse_rows)
        self.assertEqual([row.message_id for row in forward_rows], [1, 2])
        self.assertGreater(forward_rows[0].affection_hit_count, 0)
        self.assertEqual(forward_rows[0].negative_hit_count, 0)
        self.assertEqual(forward_rows[1].affection_hit_count, 0)
        self.assertEqual(forward_rows[1].negative_hit_count, 1)

        topic_rows = [row for row in forward.topic_evidence if row.topic_key == topic_key]
        self.assertEqual([row.message_id for row in topic_rows], [1, 2, 3])

    def test_store_persists_marker_subset_summary_periods_and_fk(self) -> None:
        conn = create_contract_db()
        config = AnalyticsConfig(topic_min_document_frequency=2)
        store = AnalyticsStore(conn)
        store.initialize()
        results = analyze_database(conn, config)
        run_id = store.write_run(results, config)
        self.assertEqual(run_id, 1)
        self.assertEqual(
            conn.execute("SELECT analytics_version FROM analytics_run").fetchone(),
            ("9",),
        )

        topic_key = "lexical_ngram_v1:2:dovolená chorvatsko"
        evidence = conn.execute(
            """SELECT message_id, participant_id, affection_hit_count, negative_hit_count
               FROM analysis_a4_topic_marker_evidence
               WHERE topic_key=? ORDER BY message_id""",
            (topic_key,),
        ).fetchall()
        self.assertEqual(len(evidence), 2)
        self.assertEqual([row[0] for row in evidence], [1, 2])

        summary = conn.execute(
            """SELECT topic_message_count, marker_message_count,
                      affection_message_count, negative_message_count,
                      marker_message_share
               FROM analysis_a4_topic_marker_summary WHERE topic_key=?""",
            (topic_key,),
        ).fetchone()
        self.assertEqual(summary, (3, 2, 1, 1, 0.666667))

        periods = conn.execute(
            """SELECT COUNT(*) FROM analysis_a4_topic_marker_periods
               WHERE topic_key=? AND period_kind='week'""",
            (topic_key,),
        ).fetchone()[0]
        self.assertGreater(periods, 0)
        reconciliation = conn.execute(
            "SELECT reconciliation_ok FROM analysis_a4_topic_marker_reconciliation"
        ).fetchone()[0]
        self.assertEqual(reconciliation, 1)
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
