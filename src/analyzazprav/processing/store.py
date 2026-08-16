from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import time

from .models import ProcessingResult
from .pipeline import PROCESSING_VERSION, ProcessingConfig


class ProcessingStore:
    """Persistence for A3-derived data only; A2 canonical tables stay untouched."""

    def __init__(self, conn: sqlite3.Connection, schema_path: str | Path | None = None):
        self.conn = conn
        self.schema_path = Path(schema_path) if schema_path else self._default_schema_path()

    @staticmethod
    def _default_schema_path() -> Path:
        return Path(__file__).resolve().parents[3] / "database" / "a3_schema.sql"

    _CURRENT_PROCESSED_COLUMNS = frozenset({
        "conversation_id", "membership_id", "resolved_sender_id",
        "attachment_count", "image_count", "gif_count", "video_count", "audio_count",
        "document_count", "other_media_count", "missing_attachment_count",
        "utc_year", "utc_month", "utc_day", "utc_weekday", "utc_hour",
        "local_year", "local_month", "local_day", "local_weekday", "local_hour",
    })
    _CURRENT_SENDER_RUN_COLUMNS = frozenset({
        "resolved_participant_id", "first_membership_id", "last_membership_id"
    })

    def initialize(self) -> None:
        existing = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='processed_message'"
        ).fetchone()
        needs_rebuild = False
        if existing is not None:
            columns = {row[1] for row in self.conn.execute("PRAGMA table_info(processed_message)")}
            needs_rebuild = not self._CURRENT_PROCESSED_COLUMNS.issubset(columns)

        sender_run_table = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sender_run'"
        ).fetchone()
        if sender_run_table is not None:
            sender_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(sender_run)")}
            needs_rebuild = needs_rebuild or not self._CURRENT_SENDER_RUN_COLUMNS.issubset(
                sender_columns
            )

        thread_table = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='conversation_thread'"
        ).fetchone()
        if thread_table is not None:
            session_column = next(
                (
                    row
                    for row in self.conn.execute("PRAGMA table_info(conversation_thread)")
                    if row[1] == "session_id"
                ),
                None,
            )
            needs_rebuild = needs_rebuild or session_column is None or bool(session_column[3])

        participant_table = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='resolved_participant'"
        ).fetchone()
        if existing is not None and participant_table is None:
            needs_rebuild = True

        if needs_rebuild:
            self._drop_derived_schema()
        self.conn.executescript(self.schema_path.read_text(encoding="utf-8"))

    def _drop_derived_schema(self) -> None:
        """Drop only rebuildable A3 tables when an older draft schema is detected."""
        with self.conn:
            for view in (
                "a3_analysis_messages",
                "a3_analysis_participant_aliases",
                "a3_analysis_participants",
            ):
                self.conn.execute(f"DROP VIEW IF EXISTS {view}")
            for table in (
                "processed_message",
                "a3_duplicate_candidate",
                "conversation_thread_message",
                "conversation_thread",
                "conversation_session",
                "sender_run",
                "participant_resolution_candidate",
                "participant_alias",
                "resolved_participant_member",
                "resolved_participant",
                "processing_run",
            ):
                self.conn.execute(f"DROP TABLE IF EXISTS {table}")

    def replace_all(self, result: ProcessingResult, config: ProcessingConfig) -> int:
        now_us = time.time_ns() // 1_000
        config_json = json.dumps(
            {
                "session_gap_seconds": config.session_gap_seconds,
                "duplicate_tolerance_seconds": config.duplicate_tolerance_seconds,
                "reply_relation_types": sorted(config.reply_relation_types),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        membership_count = len(result.messages)
        canonical_message_count = len({message.message_id for message in result.messages})
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO processing_run(
                       processing_version, started_at_utc_us, finished_at_utc_us,
                       status, config_json, input_message_count, output_message_count,
                       input_membership_count, output_membership_count
                   ) VALUES (?, ?, ?, 'completed', ?, ?, ?, ?, ?)""",
                (
                    PROCESSING_VERSION,
                    now_us,
                    now_us,
                    config_json,
                    canonical_message_count,
                    canonical_message_count,
                    membership_count,
                    membership_count,
                ),
            )
            run_id = int(cur.lastrowid)

            for table in (
                "processed_message",
                "a3_duplicate_candidate",
                "conversation_thread_message",
                "conversation_thread",
                "conversation_session",
                "sender_run",
                "participant_resolution_candidate",
                "participant_alias",
                "resolved_participant_member",
                "resolved_participant",
            ):
                self.conn.execute(f"DELETE FROM {table}")

            self.conn.executemany(
                """INSERT INTO resolved_participant(
                       id, canonical_name, is_self, method, confidence, processing_run_id
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        participant.id,
                        participant.canonical_name,
                        int(participant.is_self),
                        participant.method,
                        participant.confidence,
                        run_id,
                    )
                    for participant in result.resolved_participants
                ],
            )
            self.conn.executemany(
                """INSERT INTO resolved_participant_member(
                       resolved_participant_id, participant_id, method, confidence,
                       processing_run_id
                   ) VALUES (?, ?, ?, ?, ?)""",
                [
                    (
                        participant.id,
                        participant_id,
                        participant.method,
                        participant.confidence,
                        run_id,
                    )
                    for participant in result.resolved_participants
                    for participant_id in participant.member_participant_ids
                ],
            )
            self.conn.executemany(
                """INSERT INTO participant_alias(
                       resolved_participant_id, participant_id, participant_identity_id,
                       identity_type, normalized_value, original_value, method, confidence,
                       processing_run_id
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        alias.resolved_participant_id,
                        alias.participant_id,
                        alias.participant_identity_id,
                        alias.identity_type,
                        alias.normalized_value,
                        alias.original_value,
                        alias.method,
                        alias.confidence,
                        run_id,
                    )
                    for alias in result.participant_aliases
                ],
            )
            self.conn.executemany(
                """INSERT INTO participant_resolution_candidate(
                       participant_id_a, participant_id_b, reason, confidence, method,
                       processing_run_id
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        candidate.left_participant_id,
                        candidate.right_participant_id,
                        candidate.reason,
                        candidate.confidence,
                        candidate.method,
                        run_id,
                    )
                    for candidate in result.participant_resolution_candidates
                ],
            )

            self.conn.executemany(
                """INSERT INTO sender_run(
                       id, conversation_id, sender_id, resolved_participant_id,
                       first_message_id, last_message_id, first_membership_id,
                       last_membership_id, start_at_utc_us, end_at_utc_us,
                       message_count, char_count, method, processing_run_id
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        r.id,
                        r.conversation_id,
                        r.sender_id,
                        r.resolved_participant_id,
                        r.first_message_id,
                        r.last_message_id,
                        r.first_membership_id,
                        r.last_membership_id,
                        r.start_us,
                        r.end_us,
                        r.message_count,
                        r.char_count,
                        r.method,
                        run_id,
                    )
                    for r in result.sender_runs
                ],
            )
            self.conn.executemany(
                """INSERT INTO conversation_session(
                       id, conversation_id, first_message_id, last_message_id,
                       first_membership_id, last_membership_id, start_at_utc_us,
                       end_at_utc_us, message_count, gap_threshold_us, method,
                       processing_run_id
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        s.id,
                        s.conversation_id,
                        s.first_message_id,
                        s.last_message_id,
                        s.first_membership_id,
                        s.last_membership_id,
                        s.start_us,
                        s.end_us,
                        s.message_count,
                        s.gap_threshold_us,
                        s.method,
                        run_id,
                    )
                    for s in result.sessions
                ],
            )
            self.conn.executemany(
                """INSERT INTO conversation_thread(
                       id, conversation_id, session_id, method, confidence, processing_run_id
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        t.id,
                        t.conversation_id,
                        t.session_id,
                        t.method,
                        t.confidence,
                        run_id,
                    )
                    for t in result.threads
                ],
            )
            thread_rows = []
            for thread in result.threads:
                membership_ids = thread.membership_ids or (None,) * len(thread.message_ids)
                for position, (message_id, membership_id) in enumerate(
                    zip(thread.message_ids, membership_ids),
                    start=1,
                ):
                    thread_rows.append(
                        (
                            thread.id,
                            message_id,
                            thread.conversation_id,
                            membership_id,
                            position,
                        )
                    )
            self.conn.executemany(
                """INSERT INTO conversation_thread_message(
                       thread_id, message_id, conversation_id, membership_id, position
                   ) VALUES (?, ?, ?, ?, ?)""",
                thread_rows,
            )

            columns = (
                "processing_run_id", "message_id", "conversation_id", "membership_id",
                "sequence_number", "text_clean", "sender_run_id", "session_id",
                "thread_id", "resolved_sender_id",
                "char_count", "word_count", "line_count", "emoji_count",
                "question_mark_count", "exclamation_mark_count", "uppercase_ratio",
                "has_question", "has_url", "has_attachment", "attachment_count",
                "image_count", "gif_count", "video_count", "audio_count", "document_count",
                "other_media_count", "missing_attachment_count",
                "seconds_since_previous_message", "seconds_since_previous_other_sender",
                "utc_year", "utc_month", "utc_day", "utc_weekday", "utc_hour",
                "local_year", "local_month", "local_day", "local_weekday", "local_hour",
            )
            insert_sql = (
                f"INSERT INTO processed_message({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)})"
            )
            self.conn.executemany(
                insert_sql,
                [
                    (
                        run_id,
                        m.message_id,
                        m.conversation_id,
                        m.membership_id,
                        m.sequence_number,
                        m.text_clean,
                        m.sender_run_id,
                        m.session_id,
                        m.thread_id,
                        m.resolved_sender_id,
                        m.features.char_count,
                        m.features.word_count,
                        m.features.line_count,
                        m.features.emoji_count,
                        m.features.question_mark_count,
                        m.features.exclamation_mark_count,
                        m.features.uppercase_ratio,
                        int(m.features.has_question),
                        int(m.features.has_url),
                        int(m.features.has_attachment),
                        m.features.attachment_count,
                        m.features.image_count,
                        m.features.gif_count,
                        m.features.video_count,
                        m.features.audio_count,
                        m.features.document_count,
                        m.features.other_media_count,
                        m.features.missing_attachment_count,
                        m.features.seconds_since_previous_message,
                        m.features.seconds_since_previous_other_sender,
                        m.features.utc_year,
                        m.features.utc_month,
                        m.features.utc_day,
                        m.features.utc_weekday,
                        m.features.utc_hour,
                        m.features.local_year,
                        m.features.local_month,
                        m.features.local_day,
                        m.features.local_weekday,
                        m.features.local_hour,
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
                        d.left_message_id,
                        d.right_message_id,
                        d.classification,
                        d.confidence,
                        d.method,
                        run_id,
                    )
                    for d in result.duplicate_candidates
                ],
            )
        return run_id
