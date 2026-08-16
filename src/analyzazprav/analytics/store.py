from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import time
from typing import Sequence

from .config import AnalyticsConfig
from .models import ConversationAnalytics

ANALYTICS_VERSION = "2"


class AnalyticsStore:
    """Persistence for A4-derived data; A2/A3 source tables remain untouched."""

    def __init__(self, conn: sqlite3.Connection, schema_path: str | Path | None = None):
        self.conn = conn
        self.schema_path = Path(schema_path) if schema_path else self._default_schema_path()

    @staticmethod
    def _default_schema_path() -> Path:
        return Path(__file__).resolve().parents[3] / "database" / "a4_schema.sql"

    def _needs_derived_schema_rebuild(self) -> bool:
        rows = self.conn.execute("PRAGMA table_info(analytics_response_latency)").fetchall()
        if not rows:
            return False
        columns = {str(row[1]) for row in rows}
        return "response_effort_ratio" not in columns

    def _drop_derived_schema(self) -> None:
        # A4 tables contain only reproducible derived data. Rebuilding an obsolete
        # draft schema never touches A2 canonical or A3 processed source tables.
        for view in (
            "analysis_a4_events",
            "analysis_a4_changes",
            "analysis_a4_daily",
            "analysis_a4_responses",
            "analysis_a4_participants",
            "analysis_a4_conversations",
            "analysis_a4_latest_run",
        ):
            self.conn.execute(f"DROP VIEW IF EXISTS {view}")
        for table in (
            "analytics_event",
            "analytics_change_point",
            "analytics_daily_participant",
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
                               median_response_latency_seconds, median_response_effort_ratio,
                               engagement_score
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                            metrics["median_response_latency_seconds"],
                            metrics["median_response_effort_ratio"],
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
