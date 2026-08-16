from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from analyzazprav.analytics import AnalyticsConfig, AnalyticsStore, analyze_database


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
        INSERT INTO message VALUES (1), (2), (3), (4), (5);
        INSERT INTO processing_run VALUES (1, 'completed');
        INSERT INTO conversation_session VALUES (1, 10), (2, 10);

        INSERT INTO analysis_messages VALUES
            (1, 10, 1, 1000000),
            (2, 10, 1, 2000000),
            (3, 10, 2, 3000000),
            (4, 10, 2, 4000000),
            (5, 10, 1, 5000000);

        INSERT INTO processed_message VALUES
            (1, 'Dovolená Chorvatsko pláž', 1, 1, 3, 25, 0, 0, 0,
             2026, 6, 1, 1, 10, 2026, 6, 1, 1, 12),
            (2, 'Práce kancelář', 1, 2, 2, 15, 0, 0, 0,
             2026, 6, 3, 3, 10, 2026, 6, 3, 3, 12),
            (3, 'Dovolená Chorvatsko autem', 1, 3, 3, 25, 0, 0, 0,
             2026, 6, 2, 2, 10, 2026, 6, 2, 2, 12),
            (4, 'Dovolená Chorvatsko hotel', 2, 4, 3, 25, 0, 0, 0,
             2026, 6, 10, 3, 10, 2026, 6, 10, 3, 12),
            (5, 'Film večer', 2, 5, 2, 10, 0, 0, 0,
             2026, 6, 10, 3, 10, 2026, 6, 10, 3, 12);
        """
    )
    return conn


class TopicPeriodViewTests(unittest.TestCase):
    def test_topic_period_view_reports_participant_intensity(self) -> None:
        conn = create_contract_db()
        config = AnalyticsConfig(topic_min_document_frequency=2)
        store = AnalyticsStore(conn)
        store.initialize()
        results = analyze_database(conn, config)
        store.write_run(results, config)

        rows = conn.execute(
            """
            SELECT participant_id, period_start, topic_message_count,
                   participant_period_message_count, topic_message_share
            FROM analysis_a4_topic_periods
            WHERE normalized_phrase = 'dovolená chorvatsko'
              AND period_kind = 'week'
            ORDER BY period_start, participant_id
            """
        ).fetchall()
        self.assertEqual(
            rows,
            [
                (1, "2026-06-01", 1, 2, 0.5),
                (2, "2026-06-01", 1, 1, 1.0),
                (2, "2026-06-08", 1, 1, 1.0),
            ],
        )

        monthly = conn.execute(
            """
            SELECT participant_id, topic_message_count,
                   participant_period_message_count, topic_message_share
            FROM analysis_a4_topic_periods
            WHERE normalized_phrase = 'dovolená chorvatsko'
              AND period_kind = 'month'
            ORDER BY participant_id
            """
        ).fetchall()
        self.assertEqual(
            monthly,
            [
                (1, 1, 3, 0.333333),
                (2, 2, 2, 1.0),
            ],
        )

    def test_topic_period_reconciliation_accounts_for_all_evidence_rows(self) -> None:
        conn = create_contract_db()
        config = AnalyticsConfig(topic_min_document_frequency=2)
        store = AnalyticsStore(conn)
        store.initialize()
        store.write_run(analyze_database(conn, config), config)

        row = conn.execute(
            """
            SELECT evidence_row_count, dated_evidence_row_count,
                   undated_evidence_row_count,
                   unknown_participant_evidence_row_count
            FROM analysis_a4_topic_period_reconciliation
            WHERE conversation_id = 10
            """
        ).fetchone()
        self.assertIsNotNone(row)
        evidence_count, dated_count, undated_count, unknown_count = row
        self.assertEqual(evidence_count, dated_count + undated_count)
        self.assertEqual(undated_count, 0)
        self.assertEqual(unknown_count, 0)
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
