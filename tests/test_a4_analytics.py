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
    build_turns,
)


def message(
    message_id: int,
    sender: int | None,
    second: int | None,
    *,
    session: int = 1,
    sequence: int | None = None,
    text: str = "x",
    words: int = 1,
    questions: int = 0,
    exclamations: int = 0,
) -> AnalyticMessage:
    return AnalyticMessage(
        message_id=message_id,
        conversation_id=10,
        participant_id=sender,
        timestamp_us=None if second is None else second * 1_000_000,
        text_clean=text,
        session_id=session,
        sequence_number=sequence or message_id,
        word_count=words,
        character_count=len(text),
        question_mark_count=questions,
        exclamation_mark_count=exclamations,
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
            has_attachment INTEGER NOT NULL
        );
        INSERT INTO participant VALUES (1), (2);
        INSERT INTO conversation VALUES (10);
        INSERT INTO message VALUES (1), (2);
        INSERT INTO processing_run VALUES (1, 'completed');
        INSERT INTO conversation_session VALUES (1, 10);
        INSERT INTO analysis_messages VALUES
            (1, 10, 1, 0),
            (2, 10, 2, 60000000);
        INSERT INTO processed_message VALUES
            (1, 'ahoj?', 1, 1, 1, 5, 1, 0, 0),
            (2, 'čau', 1, 2, 1, 3, 0, 0, 0);
        """
    )
    return conn


class AnalyticsEngineTests(unittest.TestCase):
    def test_turns_never_cross_a3_session_boundary(self) -> None:
        turns = build_turns(
            [message(1, 1, 0, session=1), message(2, 1, 40_000, session=2)]
        )
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0].session_id, 1)
        self.assertEqual(turns[1].session_id, 2)

    def test_multiple_messages_count_as_one_response_turn(self) -> None:
        result = analyze_conversation(
            [
                message(1, 1, 0),
                message(2, 1, 10),
                message(3, 1, 20),
                message(4, 2, 50),
            ]
        )
        self.assertEqual(result.turn_count, 2)
        self.assertEqual(len(result.latency_samples), 1)
        self.assertEqual(result.latency_samples[0].latency_seconds, 30)

    def test_unknown_sender_is_preserved_but_not_attributed(self) -> None:
        result = analyze_conversation([message(1, None, 0), message(2, 1, 10)])
        self.assertEqual(result.source_message_count, 2)
        self.assertEqual(result.unknown_sender_message_count, 1)
        self.assertEqual(set(result.participant_metrics), {1})

    def test_adapter_reads_a2_a3_contract(self) -> None:
        conn = create_contract_db()
        result = analyze_database(conn)[0]
        self.assertEqual(result.conversation_id, 10)
        self.assertEqual(result.participant_metrics[1]["question_count"], 1)
        self.assertEqual(result.latency_samples[0].latency_seconds, 60)

    def test_store_persists_traceable_a4_outputs(self) -> None:
        conn = create_contract_db()
        result = analyze_database(conn)[0]
        store = AnalyticsStore(
            conn, Path(__file__).resolve().parents[1] / "database" / "a4_schema.sql"
        )
        store.initialize()
        run_id = store.write_run([result], AnalyticsConfig())

        self.assertEqual(run_id, 1)
        summary = conn.execute(
            "SELECT source_message_count, turn_count FROM analysis_a4_conversations"
        ).fetchone()
        self.assertEqual(summary, (2, 2))
        latency = conn.execute(
            "SELECT latency_seconds FROM analytics_response_latency"
        ).fetchone()
        self.assertEqual(latency, (60.0,))
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
