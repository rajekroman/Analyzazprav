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
    utc_date: str | None = None,
    local_date: str | None = None,
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
        utc_date=utc_date,
        local_date=local_date,
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
            local_year INTEGER,
            local_month INTEGER,
            local_day INTEGER
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
            (1, 'ahoj?', 1, 1, 1, 5, 1, 0, 0, 2026, 1, 1, 2026, 1, 2),
            (2, 'čau', 1, 2, 1, 3, 0, 0, 0, 2026, 1, 1, 2026, 1, 2);
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

    def test_multiple_messages_count_as_one_response_and_effort_ratio(self) -> None:
        result = analyze_conversation(
            [
                message(1, 1, 0, words=1),
                message(2, 1, 10, words=1),
                message(3, 1, 20, words=1),
                message(4, 2, 50, words=1),
            ]
        )
        self.assertEqual(result.turn_count, 2)
        self.assertEqual(len(result.response_samples), 1)
        self.assertEqual(result.response_samples[0].latency_seconds, 30)
        self.assertAlmostEqual(result.response_samples[0].response_effort_ratio, 1 / 3)
        self.assertAlmostEqual(
            result.participant_metrics[2]["median_response_effort_ratio"], 1 / 3
        )

    def test_missing_timestamp_keeps_response_effort_without_invented_latency(self) -> None:
        result = analyze_conversation(
            [message(1, 1, None, words=4), message(2, 2, None, words=2)]
        )
        self.assertEqual(len(result.response_samples), 1)
        self.assertIsNone(result.response_samples[0].latency_seconds)
        self.assertEqual(result.response_samples[0].response_effort_ratio, 0.5)

    def test_unknown_sender_is_preserved_but_not_attributed(self) -> None:
        result = analyze_conversation([message(1, None, 0), message(2, 1, 10)])
        self.assertEqual(result.source_message_count, 2)
        self.assertEqual(result.unknown_sender_message_count, 1)
        self.assertEqual(set(result.participant_metrics), {1})

    def test_adapter_prefers_a3_local_calendar_date(self) -> None:
        conn = create_contract_db()
        result = analyze_database(conn)[0]
        self.assertEqual(result.conversation_id, 10)
        self.assertEqual(result.participant_metrics[1]["question_count"], 1)
        self.assertEqual(result.response_samples[0].latency_seconds, 60)
        self.assertEqual({row.period_date for row in result.daily_metrics}, {"2026-01-02"})
        self.assertEqual({row.date_basis for row in result.daily_metrics}, {"local"})

    def test_daily_series_keeps_zero_activity_days(self) -> None:
        result = analyze_conversation(
            [
                message(1, 1, 0, session=1, local_date="2026-01-01"),
                message(2, 1, 172800, session=2, local_date="2026-01-03"),
            ]
        )
        rows = [row for row in result.daily_metrics if row.participant_id == 1]
        self.assertEqual(
            [row.period_date for row in rows],
            ["2026-01-01", "2026-01-02", "2026-01-03"],
        )
        self.assertEqual([row.message_count for row in rows], [1, 0, 1])
        self.assertEqual(rows[1].date_basis, "local")

    def test_change_point_detects_departure_from_stable_personal_baseline(self) -> None:
        source: list[AnalyticMessage] = []
        next_id = 1
        for day in range(1, 8):
            source.append(
                message(
                    next_id,
                    1,
                    day * 86400,
                    session=day,
                    local_date=f"2026-01-{day:02d}",
                )
            )
            next_id += 1
        for offset in range(6):
            source.append(
                message(
                    next_id,
                    1,
                    8 * 86400 + offset,
                    session=8,
                    local_date="2026-01-08",
                )
            )
            next_id += 1

        result = analyze_conversation(source)
        changes = [
            change
            for change in result.change_points
            if change.metric == "message_count" and change.period_date == "2026-01-08"
        ]
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].baseline_median, 1.0)
        self.assertEqual(changes[0].value, 6.0)
        self.assertEqual(changes[0].direction, "increasing")
        self.assertEqual(len(changes[0].source_message_ids), 6)

    def test_store_persists_responses_daily_series_and_change_points(self) -> None:
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
        response = conn.execute(
            "SELECT latency_seconds, response_effort_ratio FROM analysis_a4_responses"
        ).fetchone()
        self.assertEqual(response, (60.0, 1.0))
        daily_count = conn.execute("SELECT COUNT(*) FROM analysis_a4_daily").fetchone()[0]
        self.assertEqual(daily_count, 2)
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_initialize_rebuilds_obsolete_a4_derived_schema_only(self) -> None:
        conn = create_contract_db()
        conn.executescript(
            """
            CREATE TABLE analytics_run(id INTEGER PRIMARY KEY);
            CREATE TABLE analytics_response_latency(
                id INTEGER PRIMARY KEY,
                latency_seconds REAL NOT NULL
            );
            """
        )
        store = AnalyticsStore(
            conn, Path(__file__).resolve().parents[1] / "database" / "a4_schema.sql"
        )
        store.initialize()
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(analytics_response_latency)")
        }
        self.assertIn("response_effort_ratio", columns)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM message").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
