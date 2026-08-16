from __future__ import annotations

import io
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, timedelta
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
from analyzazprav.analytics.__main__ import main as analytics_main


def analytic_message(
    message_id: int,
    sender: int,
    second: int,
    *,
    session: int,
    local_date: str,
) -> AnalyticMessage:
    return AnalyticMessage(
        message_id=message_id,
        conversation_id=10,
        participant_id=sender,
        timestamp_us=second * 1_000_000,
        text_clean="x",
        session_id=session,
        sequence_number=message_id,
        word_count=1,
        character_count=1,
        question_mark_count=0,
        exclamation_mark_count=0,
        local_date=local_date,
        local_weekday=0,
        local_hour=12,
    )


def create_contract_db(path: str = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE participant(id INTEGER PRIMARY KEY);
        CREATE TABLE conversation(id INTEGER PRIMARY KEY);
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
        INSERT INTO conversation VALUES (10), (20);
        INSERT INTO processing_run VALUES (1, 'completed');
        INSERT INTO conversation_session VALUES (1, 10), (2, 20);
        INSERT INTO analysis_messages VALUES
            (1, 10, 1, 0),
            (2, 10, 2, 60000000),
            (3, 20, 1, 120000000);
        INSERT INTO processed_message VALUES
            (1, 'ahoj?', 1, 1, 1, 5, 1, 0, 0,
             2026, 1, 1, 3, 11, 2026, 1, 2, 4, 12),
            (2, 'čau', 1, 2, 1, 3, 0, 0, 0,
             2026, 1, 1, 3, 11, 2026, 1, 2, 4, 12),
            (3, 'jiná konverzace', 2, 1, 2, 15, 0, 0, 0,
             2026, 1, 1, 3, 11, 2026, 1, 2, 4, 12);
        """
    )
    return conn


class IncrementalTrendTests(unittest.TestCase):
    def test_public_analysis_reports_raw_weekly_slope(self) -> None:
        source: list[AnalyticMessage] = []
        next_id = 1
        start = date(2026, 1, 5)
        for week, count in enumerate((1, 1, 1, 1, 6)):
            period = (start + timedelta(days=7 * week)).isoformat()
            for _ in range(count):
                source.append(
                    analytic_message(
                        next_id,
                        1,
                        next_id * 10,
                        session=week + 1,
                        local_date=period,
                    )
                )
                next_id += 1

        result = analyze_conversation(source)
        trends = [
            trend
            for trend in result.trend_summaries
            if trend.period_kind == "week" and trend.metric == "message_count"
        ]
        self.assertEqual(len(trends), 1)
        self.assertEqual(trends[0].direction, "increasing")
        self.assertGreater(trends[0].slope_per_period, 0)
        self.assertEqual((trends[0].first_value, trends[0].last_value), (1.0, 6.0))

    def test_incremental_run_preserves_unchanged_latest_conversation(self) -> None:
        conn = create_contract_db()
        store = AnalyticsStore(conn)
        store.initialize()
        first = analyze_database(conn)
        self.assertEqual(store.write_run(first, AnalyticsConfig()), 1)
        self.assertEqual(analyze_incremental_database(conn), [])

        conn.execute("UPDATE processed_message SET word_count = 5 WHERE message_id = 2")
        changed = analyze_incremental_database(conn)
        self.assertEqual([item.conversation_id for item in changed], [10])
        self.assertEqual(store.write_run(changed, AnalyticsConfig()), 2)

        latest = conn.execute(
            "SELECT conversation_id, analytics_run_id FROM analysis_a4_conversations "
            "ORDER BY conversation_id"
        ).fetchall()
        self.assertEqual(latest, [(10, 2), (20, 1)])
        version = conn.execute(
            "SELECT analytics_version FROM analytics_run WHERE id = 2"
        ).fetchone()[0]
        self.assertEqual(version, "9")
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_new_a3_processing_run_invalidates_unchanged_a4_state(self) -> None:
        conn = create_contract_db()
        store = AnalyticsStore(conn)
        store.initialize()
        first = analyze_database(conn)
        self.assertEqual(store.write_run(first, AnalyticsConfig()), 1)
        self.assertEqual(analyze_incremental_database(conn), [])

        # Same logical A3 rows, but a new completed processing run establishes a
        # new provenance namespace for session/run identifiers. A4 must recompute.
        conn.execute("INSERT INTO processing_run VALUES (2, 'completed')")
        changed = analyze_incremental_database(conn)
        self.assertEqual([item.conversation_id for item in changed], [10, 20])
        run_id = store.write_run(changed, AnalyticsConfig())
        self.assertEqual(run_id, 2)
        self.assertEqual(
            conn.execute(
                "SELECT processing_run_id FROM analytics_run WHERE id=?", (run_id,)
            ).fetchone()[0],
            2,
        )

    def test_cli_full_then_incremental_noop(self) -> None:
        source = create_contract_db()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "messages.sqlite"
            target = sqlite3.connect(path)
            source.backup(target)
            target.close()

            output = io.StringIO()
            with redirect_stdout(output):
                first_rc = analytics_main([str(path), "--full"])
                second_rc = analytics_main([str(path)])
            self.assertEqual((first_rc, second_rc), (0, 0))
            lines = output.getvalue().strip().splitlines()
            self.assertIn('"status": "completed"', lines[0])
            self.assertIn('"status": "up_to_date"', lines[1])


if __name__ == "__main__":
    unittest.main()
