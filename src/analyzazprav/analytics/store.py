from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import time
from typing import Sequence

from .config import AnalyticsConfig
from .models import ConversationAnalytics

ANALYTICS_VERSION = "4"


class AnalyticsStore:
    """Persistence for A4-derived data; A2/A3 source tables remain untouched."""

    def __init__(self, conn: sqlite3.Connection, schema_path: str | Path | None = None):
        self.conn = conn
        self.schema_path = Path(schema_path) if schema_path else self._default_schema_path()

    @staticmethod
    def _default_schema_path() -> Path:
        return Path(__file__).resolve().parents[3] / "database" / "a4_schema.sql"

    def _table_exists(self, table_name: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    def _needs_derived_schema_rebuild(self) -> bool:
        latency_rows = self.conn.execute(
            "PRAGMA table_info(analytics_response_latency)"
        ).fetchall()
        if latency_rows:
            latency_columns = {str(row[1]) for row in latency_rows}
            if "response_effort_ratio" not in latency_columns:
                return True

        participant_rows = self.conn.execute(
            "PRAGMA table_info(analytics_participant_summary)"
        ).fetchall()
        if participant_rows:
            participant_columns = {str(row[1]) for row in participant_rows}
            if "mean_response_latency_seconds" not in participant_columns:
                return True
            if "unanswered_turn_count" not in participant_columns:
                return True

        if self._table_exists("analytics_run") and not self._table_exists(
            "analytics_time_bucket"
        ):
            return True
        if self._table_exists("analytics_run") and not self._table_exists(
            "analytics_silence_event"
        ):
            return True
        return False

    def _drop_derived_schema(self) -> None:
        # A4 tables contain only reproducible derived data. Rebuilding an obsolete
        # draft schema never touches A2 canonical or A3 processed source tables.
        for view in (
            "analysis_a4_events",
            "analysis_a4_regimes",
            "analysis_a4_engagement_signals",
            "analysis_a4_periods",
            "analysis_a4_changes",
            "analysis_a4_daily",
            "analysis_a4_silences",
            "analysis_a4_time_buckets",
            "analysis_a4_responses",
            "analysis_a4_participants",
            "analysis_a4_conversations",
            "analysis_a4_latest_run",
        ):
            self.conn.execute(f"DROP VIEW IF EXISTS {view}")
        for table in (
            "analytics_event",
            "analytics_dyadic_regime",
            "analytics_engagement_signal",
            "analytics_period_participant",
            "analytics_change_point",
            "analytics_daily_participant",
            "analytics_silence_event",
            "analytics_time_bucket",
            "analytics_response_latency",
            "analytics_participant_summary",
            "analytics_conversation_summary",
            "analytics_run",
        ):
            self.conn.execute(f"DROP TABLE IF EXISTS {table}")

    def initialize(self) -> None:
        with self.conn:
            if self._needs_derived_schema_rebuild():
                self._drop_derived_schema()
            self.conn.executescript(self.schema_path.read_text(encoding="utf-8"))

    def latest_processing_run_id(self) -> int:
        row = self.conn.execute(
            "SELECT id FROM processing_run WHERE status = 'completed' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            raise RuntimeError("A4 requires a completed A3 processing_run")
        return int(row[0])

    def write_run(
        self,
        results: Sequence[ConversationAnalytics],
        config: AnalyticsConfig,
        processing_run_id: int | None = None,
    ) -> int:
        processing_id = processing_run_id or self.latest_processing_run_id()
        now_us = time.time_ns() // 1_000
        config_json = json.dumps(config.as_dict(), sort_keys=True, ensure_ascii=False)
        input_message_count = sum(result.source_message_count for result in results)

        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO analytics_run(
                       analytics_version, processing_run_id, started_at_utc_us,
                       finished_at_utc_us, status, config_json,
                       conversation_count, input_message_count
                   ) VALUES (?, ?, ?, ?, 'completed', ?, ?, ?)""",
                (
                    ANALYTICS_VERSION,
                    processing_id,
                    now_us,
                    now_us,
                    config_json,
                    len(results),
                    input_message_count,
                ),
            )
            run_id = int(cur.lastrowid)

            for result in results:
                reciprocity = result.reciprocity
                self.conn.execute(
                    """INSERT INTO analytics_conversation_summary(
                           analytics_run_id, conversation_id, source_message_count,
                           known_sender_message_count, unknown_sender_message_count,
                           turn_count, session_count, message_reciprocity, word_reciprocity,
                           turn_reciprocity, initiation_reciprocity
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        run_id,
                        result.conversation_id,
                        result.source_message_count,
                        result.known_sender_message_count,
                        result.unknown_sender_message_count,
                        result.turn_count,
                        result.session_count,
                        reciprocity.get("message_reciprocity"),
                        reciprocity.get("word_reciprocity"),
                        reciprocity.get("turn_reciprocity"),
                        reciprocity.get("initiation_reciprocity"),
                    ),
                )
                for participant_id, metrics in result.participant_metrics.items():
                    self.conn.execute(
                        """INSERT INTO analytics_participant_summary(
                               analytics_run_id, conversation_id, participant_id,
                               message_count, word_count, character_count, active_days, turn_count,
                               initiations, initiation_share, question_count, exclamation_count,
                               affection_marker_count, negative_marker_count,
                               response_turn_count, latency_sample_count, unanswered_turn_count,
                               mean_response_latency_seconds, median_response_latency_seconds,
                               p25_response_latency_seconds, p75_response_latency_seconds,
                               p90_response_latency_seconds, median_response_effort_ratio,
                               clock_known_message_count, weekend_message_count,
                               night_message_count, engagement_score
                           ) VALUES (
                               ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                           )""",
                        (
                            run_id,
                            result.conversation_id,
                            participant_id,
                            metrics["message_count"],
                            metrics["word_count"],
                            metrics["character_count"],
                            metrics["active_days"],
                            metrics["turn_count"],
                            metrics["initiations"],
                            metrics["initiation_share"],
                            metrics["question_count"],
                            metrics["exclamation_count"],
                            metrics["affection_marker_count"],
                            metrics["negative_marker_count"],
                            metrics["response_turn_count"],
                            metrics["latency_sample_count"],
                            metrics["unanswered_turn_count"],
                            metrics["mean_response_latency_seconds"],
                            metrics["median_response_latency_seconds"],
                            metrics["p25_response_latency_seconds"],
                            metrics["p75_response_latency_seconds"],
                            metrics["p90_response_latency_seconds"],
                            metrics["median_response_effort_ratio"],
                            metrics["clock_known_message_count"],
                            metrics["weekend_message_count"],
                            metrics["night_message_count"],
                            metrics["engagement_score"],
                        ),
                    )
                self.conn.executemany(
                    """INSERT INTO analytics_response_latency(
                           analytics_run_id, conversation_id, session_id,
                           from_participant_id, responder_id, previous_turn_id,
                           response_turn_id, latency_seconds, response_effort_ratio
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            run_id,
                            sample.conversation_id,
                            sample.session_id,
                            sample.from_participant_id,
                            sample.responder_id,
                            sample.previous_turn_id,
                            sample.response_turn_id,
                            sample.latency_seconds,
                            sample.response_effort_ratio,
                        )
                        for sample in result.response_samples
                    ],
                )
                self.conn.executemany(
                    """INSERT INTO analytics_time_bucket(
                           analytics_run_id, conversation_id, participant_id,
                           time_basis, bucket_kind, bucket_value, message_count,
                           source_message_ids_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            run_id,
                            row.conversation_id,
                            row.participant_id,
                            row.time_basis,
                            row.bucket_kind,
                            row.bucket_value,
                            row.message_count,
                            json.dumps(row.source_message_ids),
                        )
                        for row in result.time_buckets
                    ],
                )
                self.conn.executemany(
                    """INSERT INTO analytics_silence_event(
                           analytics_run_id, conversation_id, previous_session_id,
                           next_session_id, gap_seconds, previous_turn_id, return_turn_id,
                           before_participant_id, return_participant_id,
                           source_message_ids_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            run_id,
                            event.conversation_id,
                            event.previous_session_id,
                            event.next_session_id,
                            event.gap_seconds,
                            event.previous_turn_id,
                            event.return_turn_id,
                            event.before_participant_id,
                            event.return_participant_id,
                            json.dumps(event.source_message_ids),
                        )
                        for event in result.silence_events
                    ],
                )
                self.conn.executemany(
                    """INSERT INTO analytics_daily_participant(
                           analytics_run_id, conversation_id, participant_id,
                           period_date, date_basis, message_count, word_count,
                           turn_count, initiations, question_count, affection_marker_count,
                           negative_marker_count, median_response_latency_seconds,
                           median_response_effort_ratio, source_message_ids_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            run_id,
                            row.conversation_id,
                            row.participant_id,
                            row.period_date,
                            row.date_basis,
                            row.message_count,
                            row.word_count,
                            row.turn_count,
                            row.initiations,
                            row.question_count,
                            row.affection_marker_count,
                            row.negative_marker_count,
                            row.median_response_latency_seconds,
                            row.median_response_effort_ratio,
                            json.dumps(row.source_message_ids),
                        )
                        for row in result.daily_metrics
                    ],
                )
                self.conn.executemany(
                    """INSERT INTO analytics_period_participant(
                           analytics_run_id, conversation_id, participant_id, period_kind,
                           period_start, period_end, date_basis, message_count, word_count,
                           turn_count, initiations, question_count, affection_marker_count,
                           negative_marker_count, median_response_latency_seconds,
                           median_response_effort_ratio, source_message_ids_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            run_id, row.conversation_id, row.participant_id, row.period_kind,
                            row.period_start, row.period_end, row.date_basis, row.message_count,
                            row.word_count, row.turn_count, row.initiations, row.question_count,
                            row.affection_marker_count, row.negative_marker_count,
                            row.median_response_latency_seconds, row.median_response_effort_ratio,
                            json.dumps(row.source_message_ids),
                        )
                        for row in result.period_metrics
                    ],
                )
                self.conn.executemany(
                    """INSERT INTO analytics_engagement_signal(
                           analytics_run_id, conversation_id, participant_id, period_start,
                           period_end, score, direction, component_scores_json,
                           source_message_ids_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            run_id, signal.conversation_id, signal.participant_id,
                            signal.period_start, signal.period_end, signal.score, signal.direction,
                            json.dumps(signal.component_scores, sort_keys=True),
                            json.dumps(signal.source_message_ids),
                        )
                        for signal in result.engagement_signals
                    ],
                )
                self.conn.executemany(
                    """INSERT INTO analytics_dyadic_regime(
                           analytics_run_id, conversation_id, period_start, period_end,
                           participant_a_id, participant_a_direction, participant_a_score,
                           participant_b_id, participant_b_direction, participant_b_score,
                           regime_type, source_message_ids_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            run_id, regime.conversation_id, regime.period_start, regime.period_end,
                            regime.participant_a_id, regime.participant_a_direction,
                            regime.participant_a_score, regime.participant_b_id,
                            regime.participant_b_direction, regime.participant_b_score,
                            regime.regime_type, json.dumps(regime.source_message_ids),
                        )
                        for regime in result.dyadic_regimes
                    ],
                )
                self.conn.executemany(
                    """INSERT INTO analytics_change_point(
                           analytics_run_id, conversation_id, participant_id,
                           metric, period_date, value, baseline_median,
                           robust_z_score, direction, source_message_ids_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            run_id,
                            change.conversation_id,
                            change.participant_id,
                            change.metric,
                            change.period_date,
                            change.value,
                            change.baseline_median,
                            change.robust_z_score,
                            change.direction,
                            json.dumps(change.source_message_ids),
                        )
                        for change in result.change_points
                    ],
                )
                self.conn.executemany(
                    """INSERT INTO analytics_event(
                           analytics_run_id, conversation_id, session_id, event_type,
                           score, start_at_utc_us, end_at_utc_us,
                           factors_json, source_message_ids_json
                       ) VALUES (?, ?, ?, 'conflict_candidate', ?, ?, ?, ?, ?)""",
                    [
                        (
                            run_id,
                            event.conversation_id,
                            event.session_id,
                            event.score,
                            event.start_us,
                            event.end_us,
                            json.dumps(event.factors, sort_keys=True),
                            json.dumps(event.source_message_ids),
                        )
                        for event in result.conflicts
                    ],
                )
        return run_id
