from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import time

from .models import ProcessingResult
from .pipeline import PROCESSING_VERSION, ProcessingConfig


class ProcessingStore:
    """Persistence for immutable A3-derived processing runs only."""

    def __init__(self, conn: sqlite3.Connection, schema_path: str | Path | None = None):
        self.conn = conn
        self.schema_path = Path(schema_path) if schema_path else self._default_schema_path()

    @staticmethod
    def _default_schema_path() -> Path:
        return Path(__file__).resolve().parents[3] / "database" / "a3_schema.sql"

    _CURRENT_PROCESSED_COLUMNS = frozenset(
        {
            "processing_run_id", "membership_id", "message_id", "conversation_id",
            "resolved_sender_id", "attachment_count", "image_count", "gif_count",
            "video_count", "audio_count", "document_count", "other_media_count",
            "missing_attachment_count", "utc_year", "utc_month", "utc_day",
            "utc_weekday", "utc_hour", "local_year", "local_month", "local_day",
            "local_weekday", "local_hour",
        }
    )
    _CURRENT_RUN_COLUMNS = frozenset(
        {"input_membership_count", "canonical_message_count", "output_membership_count"}
    )

    def initialize(self) -> None:
        needs_rebuild = False
        processed = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='processed_message'"
        ).fetchone()
        if processed is not None:
            columns = {row[1] for row in self.conn.execute("PRAGMA table_info(processed_message)")}
            needs_rebuild = not self._CURRENT_PROCESSED_COLUMNS.issubset(columns)
        run_table = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='processing_run'"
        ).fetchone()
        if run_table is not None:
            columns = {row[1] for row in self.conn.execute("PRAGMA table_info(processing_run)")}
            needs_rebuild = needs_rebuild or not self._CURRENT_RUN_COLUMNS.issubset(columns)
        resolved = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='resolved_participant'"
        ).fetchone()
        if processed is not None and resolved is None:
            needs_rebuild = True
        if needs_rebuild:
            self._drop_derived_schema()
        self.conn.executescript(self.schema_path.read_text(encoding="utf-8"))

    def _drop_derived_schema(self) -> None:
        self.conn.execute("PRAGMA foreign_keys = OFF")
        try:
            with self.conn:
                for view in (
                    "analysis_participant_aliases_latest",
                    "analysis_resolved_participants_latest",
                    "analysis_processed_messages_latest",
                ):
                    self.conn.execute(f"DROP VIEW IF EXISTS {view}")
                for table in (
                    "processed_message", "a3_duplicate_candidate",
                    "conversation_thread_message", "conversation_thread",
                    "conversation_session", "sender_run",
                    "participant_resolution_candidate", "participant_alias",
                    "resolved_participant_member", "resolved_participant", "processing_run",
                ):
                    self.conn.execute(f"DROP TABLE IF EXISTS {table}")
        finally:
            self.conn.execute("PRAGMA foreign_keys = ON")

    @staticmethod
    def _config_json(config: ProcessingConfig) -> str:
        return json.dumps(
            {
                "session_gap_seconds": config.session_gap_seconds,
                "duplicate_tolerance_seconds": config.duplicate_tolerance_seconds,
                "reply_relation_types": sorted(config.reply_relation_types),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    def persist(self, result: ProcessingResult, config: ProcessingConfig) -> int:
        now_us = time.time_ns() // 1_000
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO processing_run(
                       processing_version, started_at_utc_us, finished_at_utc_us,
                       status, config_json, input_membership_count,
                       canonical_message_count, output_membership_count
                   ) VALUES (?, ?, ?, 'completed', ?, ?, ?, ?)""",
                (
                    PROCESSING_VERSION, now_us, now_us, self._config_json(config),
                    len(result.messages), len({m.message_id for m in result.messages}),
                    len(result.messages),
                ),
            )
            run_id = int(cur.lastrowid)

            self.conn.executemany(
                """INSERT INTO resolved_participant(
                       processing_run_id, id, canonical_name, is_self, method, confidence
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (run_id, p.id, p.canonical_name, int(p.is_self), p.method, p.confidence)
                    for p in result.resolved_participants
                ],
            )
            self.conn.executemany(
                """INSERT INTO resolved_participant_member(
                       processing_run_id, resolved_participant_id, participant_id,
                       method, confidence
                   ) VALUES (?, ?, ?, ?, ?)""",
                [
                    (run_id, p.id, participant_id, p.method, p.confidence)
                    for p in result.resolved_participants
                    for participant_id in p.member_participant_ids
                ],
            )
            self.conn.executemany(
                """INSERT INTO participant_alias(
                       processing_run_id, resolved_participant_id, participant_id,
                       participant_identity_id, identity_type, normalized_value,
                       original_value, method, confidence
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        run_id, a.resolved_participant_id, a.participant_id,
                        a.participant_identity_id, a.identity_type, a.normalized_value,
                        a.original_value, a.method, a.confidence,
                    )
                    for a in result.participant_aliases
                ],
            )
            self.conn.executemany(
                """INSERT INTO participant_resolution_candidate(
                       processing_run_id, participant_id_a, participant_id_b,
                       reason, confidence, method
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        run_id, c.left_participant_id, c.right_participant_id,
                        c.reason, c.confidence, c.method,
                    )
                    for c in result.participant_resolution_candidates
                ],
            )
            self.conn.executemany(
                """INSERT INTO sender_run(
                       processing_run_id, id, conversation_id, sender_id,
                       resolved_participant_id, first_membership_id, last_membership_id,
                       first_message_id, last_message_id, start_at_utc_us, end_at_utc_us,
                       message_count, char_count, method
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        run_id, r.id, r.conversation_id, r.sender_id,
                        r.resolved_participant_id, r.first_membership_id,
                        r.last_membership_id, r.first_message_id, r.last_message_id,
                        r.start_us, r.end_us, r.message_count, r.char_count, r.method,
                    )
                    for r in result.sender_runs
                ],
            )
            self.conn.executemany(
                """INSERT INTO conversation_session(
                       processing_run_id, id, conversation_id,
                       first_membership_id, last_membership_id,
                       first_message_id, last_message_id, start_at_utc_us,
                       end_at_utc_us, message_count, gap_threshold_us, method
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        run_id, s.id, s.conversation_id, s.first_membership_id,
                        s.last_membership_id, s.first_message_id, s.last_message_id,
                        s.start_us, s.end_us, s.message_count, s.gap_threshold_us, s.method,
                    )
                    for s in result.sessions
                ],
            )
            self.conn.executemany(
                """INSERT INTO conversation_thread(
                       processing_run_id, id, conversation_id, session_id, method, confidence
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (run_id, t.id, t.conversation_id, t.session_id, t.method, t.confidence)
                    for t in result.threads
                ],
            )
            self.conn.executemany(
                """INSERT INTO conversation_thread_message(
                       processing_run_id, thread_id, membership_id, message_id, position
                   ) VALUES (?, ?, ?, ?, ?)""",
                [
                    (run_id, t.id, membership_id, message_id, position)
                    for t in result.threads
                    for position, (membership_id, message_id) in enumerate(
                        zip(t.membership_ids, t.message_ids), start=1
                    )
                ],
            )

            columns = (
                "processing_run_id", "membership_id", "message_id", "conversation_id",
                "sequence_number", "text_clean", "sender_run_id", "session_id",
                "thread_id", "resolved_sender_id", "char_count", "word_count",
                "line_count", "emoji_count", "question_mark_count",
                "exclamation_mark_count", "uppercase_ratio", "has_question", "has_url",
                "has_attachment", "attachment_count", "image_count", "gif_count",
                "video_count", "audio_count", "document_count", "other_media_count",
                "missing_attachment_count", "seconds_since_previous_message",
                "seconds_since_previous_other_sender", "utc_year", "utc_month", "utc_day",
                "utc_weekday", "utc_hour", "local_year", "local_month", "local_day",
                "local_weekday", "local_hour",
            )
            insert_sql = (
                f"INSERT INTO processed_message({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)})"
            )
            self.conn.executemany(
                insert_sql,
                [
                    (
                        run_id, m.membership_id, m.message_id, m.conversation_id,
                        m.sequence_number, m.text_clean, m.sender_run_id, m.session_id,
                        m.thread_id, m.resolved_sender_id, m.features.char_count,
                        m.features.word_count, m.features.line_count, m.features.emoji_count,
                        m.features.question_mark_count, m.features.exclamation_mark_count,
                        m.features.uppercase_ratio, int(m.features.has_question),
                        int(m.features.has_url), int(m.features.has_attachment),
                        m.features.attachment_count, m.features.image_count,
                        m.features.gif_count, m.features.video_count, m.features.audio_count,
                        m.features.document_count, m.features.other_media_count,
                        m.features.missing_attachment_count,
                        m.features.seconds_since_previous_message,
                        m.features.seconds_since_previous_other_sender,
                        m.features.utc_year, m.features.utc_month, m.features.utc_day,
                        m.features.utc_weekday, m.features.utc_hour, m.features.local_year,
                        m.features.local_month, m.features.local_day,
                        m.features.local_weekday, m.features.local_hour,
                    )
                    for m in result.messages
                ],
            )
            self.conn.executemany(
                """INSERT INTO a3_duplicate_candidate(
                       message_id_a, message_id_b, classification, confidence, method,
                       processing_run_id
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        d.left_message_id, d.right_message_id, d.classification,
                        d.confidence, d.method, run_id,
                    )
                    for d in result.duplicate_candidates
                ],
            )
        return run_id

    def replace_all(self, result: ProcessingResult, config: ProcessingConfig) -> int:
        return self.persist(result, config)
