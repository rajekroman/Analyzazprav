from __future__ import annotations

import sqlite3
import sys
import unittest
from datetime import date, timedelta
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
    utc_weekday: int | None = None,
    utc_hour: int | None = None,
    local_weekday: int | None = None,
    local_hour: int | None = None,
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
        utc_weekday=utc_weekday,
        utc_hour=utc_hour,
        local_weekday=local_weekday,
        local_hour=local_hour,
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
        INSERT INTO message VALUES (1), (2);
        INSERT INTO processing_run VALUES (1, 'completed');
        INSERT INTO conversation_session VALUES (1, 10);
        INSERT INTO analysis_messages VALUES
            (1, 10, 1, 0),
            (2, 10, 2, 60000000);
        INSERT INTO processed_message VALUES
            (1, 'ahoj?', 1, 1, 1, 5, 1, 0, 0,
             2026, 1, 1, 3, 23, 2026, 1, 2, 4, 1),
            (2, 'čau', 1, 2, 1, 3, 0, 0, 0,
             2026, 1, 1, 3, 23, 2026, 1, 2, 4, 12);
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
        self.assertEqual(result.participant_metrics[2]["response_turn_count"], 1)
        self.assertEqual(result.participant_metrics[2]["latency_sample_count"], 0)

    def test_latency_distribution_and_unanswered_turns(self) -> None:
        source = [
            message(1, 1, 0),
            message(2, 2, 10),
            message(3, 1, 15),
            message(4, 2, 35),
            message(5, 1, 40),
            message(6, 2, 70),
            message(7, 1, 75),
            message(8, 2, 115),
        ]
        result = analyze_conversation(source)
        metrics = result.participant_metrics[2]
        self.assertEqual(metrics["response_turn_count"], 4)
        self.assertEqual(metrics["latency_sample_count"], 4)
        self.assertEqual(metrics["unanswered_turn_count"], 1)
        self.assertEqual(metrics["mean_response_latency_seconds"], 25)
        self.assertEqual(metrics["median_response_latency_seconds"], 25)
        self.assertEqual(metrics["p25_response_latency_seconds"], 17.5)
        self.assertEqual(metrics["p75_response_latency_seconds"], 32.5)
        self.assertEqual(metrics["p90_response_latency_seconds"], 37.0)
        self.assertEqual(result.participant_metrics[1]["unanswered_turn_count"], 0)

    def test_unknown_sender_is_preserved_but_not_attributed(self) -> None:
        result = analyze_conversation([message(1, None, 0), message(2, 1, 10)])
        self.assertEqual(result.source_message_count, 2)
        self.assertEqual(result.unknown_sender_message_count, 1)
        self.assertEqual(set(result.participant_metrics), {1})

    def test_adapter_prefers_a3_local_calendar_and_clock_fields(self) -> None:
        conn = create_contract_db()
        result = analyze_database(conn)[0]
        self.assertEqual(result.conversation_id, 10)
        self.assertEqual(result.participant_metrics[1]["question_count"], 1)
        self.assertEqual(result.response_samples[0].latency_seconds, 60)
        self.assertEqual({row.period_date for row in result.daily_metrics}, {"2026-01-02"})
        self.assertEqual({row.date_basis for row in result.daily_metrics}, {"local"})
        participant_one_hours = [
            row for row in result.time_buckets
            if row.participant_id == 1 and row.bucket_kind == "hour"
        ]
        self.assertEqual(len(participant_one_hours), 1)
        self.assertEqual(participant_one_hours[0].time_basis, "local")
        self.assertEqual(participant_one_hours[0].bucket_value, "01")

    def test_time_buckets_use_local_then_utc_fallback(self) -> None:
        result = analyze_conversation(
            [
                message(1, 1, 0, local_weekday=5, local_hour=1),
                message(2, 1, 10, local_weekday=1, local_hour=12),
                message(3, 1, 20, utc_weekday=6, utc_hour=2),
            ]
        )
        metrics = result.participant_metrics[1]
        self.assertEqual(metrics["clock_known_message_count"], 3)
        self.assertEqual(metrics["weekend_message_count"], 2)
        self.assertEqual(metrics["night_message_count"], 2)
        local_hour_one = [
            row for row in result.time_buckets
            if row.time_basis == "local"
            and row.bucket_kind == "hour"
            and row.bucket_value == "01"
        ]
        utc_hour_two = [
            row for row in result.time_buckets
            if row.time_basis == "utc"
            and row.bucket_kind == "hour"
            and row.bucket_value == "02"
        ]
        self.assertEqual(local_hour_one[0].source_message_ids, (1,))
        self.assertEqual(utc_hour_two[0].source_message_ids, (3,))

    def test_long_silence_identifies_return_participant(self) -> None:
        result = analyze_conversation(
            [
                message(1, 1, 0, session=1),
                message(2, 2, 60, session=1),
                message(3, 1, 90_060, session=2),
            ],
            AnalyticsConfig(long_silence_seconds=24 * 60 * 60),
        )
        self.assertEqual(len(result.silence_events), 1)
        event = result.silence_events[0]
        self.assertEqual(event.previous_session_id, 1)
        self.assertEqual(event.next_session_id, 2)
        self.assertEqual(event.gap_seconds, 90_000)
        self.assertEqual(event.before_participant_id, 2)
        self.assertEqual(event.return_participant_id, 1)
        self.assertEqual(event.source_message_ids, (2, 3))

    def test_daily_series_keeps_zero_activity_days(self) -> None:
        result = analyze_conversation(
            [
                message(1, 1, 0, session=1, local_date="2026-01-01"),
                message(2, 1, 172800, session=2, local_date="2026-01-03"),
            ]
        )
        rows = [row for row in result.daily_metrics if row.participant_id == 1]
        self.assertEqual([row.period_date for row in rows], ["2026-01-01", "2026-01-02", "2026-01-03"])
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

    def test_weekly_monthly_aggregation_and_mutual_approach_regime(self) -> None:
        source: list[AnalyticMessage] = []
        next_id = 1
        next_session = 1
        start = date(2026, 1, 5)
        for week in range(4):
            day = (start + timedelta(days=7 * week)).isoformat()
            for participant in (1, 2):
                source.append(
                    message(
                        next_id, participant, next_id * 10, session=next_session,
                        sequence=next_id, local_date=day,
                    )
                )
                next_id += 1
                next_session += 1

        current_day = (start + timedelta(days=28)).isoformat()
        for participant in (1, 2):
            session_id = next_session
            next_session += 1
            for _ in range(6):
                source.append(
                    message(
                        next_id, participant, next_id * 10, session=session_id,
                        sequence=next_id, local_date=current_day,
                    )
                )
                next_id += 1

        result = analyze_conversation(source)
        weekly = [row for row in result.period_metrics if row.period_kind == "week"]
        monthly = [row for row in result.period_metrics if row.period_kind == "month"]
        self.assertEqual(len(weekly), 10)
        self.assertGreaterEqual(len(monthly), 2)

        current_signals = [
            signal for signal in result.engagement_signals
            if signal.period_start == current_day
        ]
        self.assertEqual(len(current_signals), 2)
        self.assertEqual({signal.direction for signal in current_signals}, {"increase"})
        regimes = [
            regime for regime in result.dyadic_regimes
            if regime.period_start == current_day
        ]
        self.assertEqual(len(regimes), 1)
        self.assertEqual(regimes[0].regime_type, "mutual_approach")
        self.assertEqual(len(regimes[0].source_message_ids), 12)

    def test_store_persists_response_time_and_silence_outputs(self) -> None:
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
        participant = conn.execute(
            """SELECT response_turn_count, latency_sample_count, unanswered_turn_count,
                      mean_response_latency_seconds, p90_response_latency_seconds
               FROM analysis_a4_participants WHERE participant_id = 2"""
        ).fetchone()
        self.assertEqual(participant, (1, 1, 1, 60.0, 60.0))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM analysis_a4_time_buckets").fetchone()[0], 8)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM analysis_a4_silences").fetchone()[0], 0)
        daily_count = conn.execute("SELECT COUNT(*) FROM analysis_a4_daily").fetchone()[0]
        self.assertEqual(daily_count, 2)
        period_count = conn.execute("SELECT COUNT(*) FROM analysis_a4_periods").fetchone()[0]
        self.assertEqual(period_count, 4)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM analysis_a4_engagement_signals").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM analysis_a4_regimes").fetchone()[0], 0)
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
        participant_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(analytics_participant_summary)")
        }
        self.assertIn("response_effort_ratio", columns)
        self.assertIn("mean_response_latency_seconds", participant_columns)
        self.assertTrue(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='analytics_time_bucket'"
        ).fetchone())
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM message").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
