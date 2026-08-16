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
    analyze_incremental_database,
)


def message(
    message_id: int,
    text: str,
    participant_id: int,
    *,
    conversation_id: int = 10,
    session_id: int = 1,
    period_date: str | None = None,
) -> AnalyticMessage:
    return AnalyticMessage(
        message_id=message_id,
        conversation_id=conversation_id,
        participant_id=participant_id,
        timestamp_us=message_id * 1_000_000,
        text_clean=text,
        session_id=session_id,
        sequence_number=message_id,
        word_count=len(text.split()),
        character_count=len(text),
        question_mark_count=text.count("?"),
        exclamation_mark_count=text.count("!"),
        local_date=period_date,
        local_weekday=0 if period_date else None,
        local_hour=12 if period_date else None,
    )


def create_topic_contract_db() -> sqlite3.Connection:
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
        INSERT INTO message VALUES (1), (2), (3), (4), (5), (6);
        INSERT INTO processing_run VALUES (1, 'completed');
        INSERT INTO conversation_session VALUES (1, 10);

        INSERT INTO analysis_messages VALUES
            (1, 10, 1, 1000000),
            (2, 10, 2, 2000000),
            (3, 10, 1, 3000000),
            (4, 10, 2, 4000000),
            (5, 10, 1, 5000000),
            (6, 10, 2, 6000000);

        INSERT INTO processed_message VALUES
            (1, 'Dovolená Chorvatsko moře', 1, 1, 3, 24, 0, 0, 0,
             2026, 6, 1, 0, 10, 2026, 6, 1, 0, 12),
            (2, 'Dovolená Chorvatsko autem', 1, 2, 3, 25, 0, 0, 0,
             2026, 6, 2, 1, 10, 2026, 6, 2, 1, 12),
            (3, 'Chorvatsko dovolená pláž', 1, 3, 3, 24, 0, 0, 0,
             2026, 6, 3, 2, 10, 2026, 6, 3, 2, 12),
            (4, 'Dovolená Chorvatsko hotel', 1, 4, 3, 25, 0, 0, 0,
             2026, 6, 4, 3, 10, 2026, 6, 4, 3, 12),
            (5, 'práce meeting kancelář', 1, 5, 3, 21, 0, 0, 0,
             2026, 6, 5, 4, 10, 2026, 6, 5, 4, 12),
            (6, 'film večer', 1, 6, 2, 10, 0, 0, 0,
             2026, 6, 6, 5, 10, 2026, 6, 6, 5, 12);
        """
    )
    return conn


class TopicAnalyticsTests(unittest.TestCase):
    def test_lexical_topics_are_deterministic_and_traceable(self) -> None:
        source = [
            message(1, "Dovolená Chorvatsko moře", 1, period_date="2026-06-01"),
            message(2, "Dovolená Chorvatsko autem", 2, period_date="2026-06-02"),
            message(3, "Chorvatsko dovolená pláž", 1, period_date="2026-06-03"),
            message(4, "Dovolená Chorvatsko hotel", 2, period_date="2026-06-04"),
            message(5, "práce meeting kancelář", 1, period_date="2026-06-05"),
            message(6, "film večer", 2, period_date="2026-06-06"),
        ]
        config = AnalyticsConfig(topic_min_document_frequency=2)

        forward = analyze_conversation(source, config)
        reverse = analyze_conversation(list(reversed(source)), config)

        forward_snapshot = [
            (
                item.topic_key,
                item.document_frequency,
                item.occurrence_count,
                item.source_message_ids,
            )
            for item in forward.topic_candidates
        ]
        reverse_snapshot = [
            (
                item.topic_key,
                item.document_frequency,
                item.occurrence_count,
                item.source_message_ids,
            )
            for item in reverse.topic_candidates
        ]
        self.assertEqual(forward_snapshot, reverse_snapshot)

        by_phrase = {
            item.normalized_phrase: item for item in forward.topic_candidates
        }
        self.assertIn("dovolená chorvatsko", by_phrase)
        topic = by_phrase["dovolená chorvatsko"]
        self.assertEqual(topic.document_frequency, 3)
        self.assertEqual(topic.participant_count, 2)
        self.assertEqual(topic.source_message_ids, (1, 2, 4))

        evidence = [
            row
            for row in forward.topic_evidence
            if row.topic_key == topic.topic_key
        ]
        self.assertEqual([row.message_id for row in evidence], [1, 2, 4])
        self.assertEqual(
            [row.period_date for row in evidence],
            ["2026-06-01", "2026-06-02", "2026-06-04"],
        )

    def test_stopwords_do_not_become_standalone_topics(self) -> None:
        source = [
            message(1, "to je a to je dovolená", 1),
            message(2, "to je dovolená a je to", 2),
            message(3, "dovolená to je", 1),
        ]
        result = analyze_conversation(
            source, AnalyticsConfig(topic_min_document_frequency=2)
        )
        phrases = {item.normalized_phrase for item in result.topic_candidates}
        self.assertIn("dovolená", phrases)
        self.assertTrue({"to", "je", "a"}.isdisjoint(phrases))

    def test_v6_store_persists_topic_summary_and_message_evidence(self) -> None:
        conn = create_topic_contract_db()
        config = AnalyticsConfig(topic_min_document_frequency=2)
        store = AnalyticsStore(conn)
        store.initialize()
        results = analyze_database(conn, config)
        run_id = store.write_run(results, config)

        self.assertEqual(run_id, 1)
        self.assertEqual(
            conn.execute(
                "SELECT analytics_version FROM analytics_run WHERE id = 1"
            ).fetchone(),
            ("6",),
        )
        topic = conn.execute(
            """SELECT normalized_phrase, document_frequency
               FROM analysis_a4_topics
               WHERE normalized_phrase = 'dovolená chorvatsko'"""
        ).fetchone()
        self.assertEqual(topic, ("dovolená chorvatsko", 3))
        evidence = conn.execute(
            """SELECT message_id, participant_id, period_date
               FROM analysis_a4_topic_evidence
               WHERE topic_key = 'lexical_ngram_v1:2:dovolená chorvatsko'
               ORDER BY message_id"""
        ).fetchall()
        self.assertEqual(
            evidence,
            [
                (1, 1, "2026-06-01"),
                (2, 2, "2026-06-02"),
                (4, 2, "2026-06-04"),
            ],
        )
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_incremental_mode_recomputes_when_analysis_config_changes(self) -> None:
        conn = create_topic_contract_db()
        first_config = AnalyticsConfig(topic_min_document_frequency=2)
        store = AnalyticsStore(conn)
        store.initialize()
        first = analyze_database(conn, first_config)
        store.write_run(first, first_config)

        self.assertEqual(analyze_incremental_database(conn, first_config), [])

        second_config = AnalyticsConfig(topic_min_document_frequency=3)
        changed = analyze_incremental_database(conn, second_config)
        self.assertEqual([item.conversation_id for item in changed], [10])


if __name__ == "__main__":
    unittest.main()
