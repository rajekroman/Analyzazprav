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

    def initialize(self) -> None:
        self.conn.executescript(self.schema_path.read_text(encoding="utf-8"))

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
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO processing_run(
                       processing_version, started_at_utc_us, finished_at_utc_us,
                       status, config_json, input_message_count, output_message_count
                   ) VALUES (?, ?, ?, 'completed', ?, ?, ?)""",
                (PROCESSING_VERSION, now_us, now_us, config_json, len(result.messages), len(result.messages)),
            )
            run_id = int(cur.lastrowid)

            for table in (
                "processed_message",
                "a3_duplicate_candidate",
                "conversation_thread_message",
                "conversation_thread",
                "conversation_session",
                "sender_run",
            ):
                self.conn.execute(f"DELETE FROM {table}")

            self.conn.executemany(
                """INSERT INTO sender_run(
                       id, conversation_id, sender_id, first_message_id, last_message_id,
                       start_at_utc_us, end_at_utc_us, message_count, char_count, method, processing_run_id
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (r.id, r.conversation_id, r.sender_id, r.first_message_id, r.last_message_id,
                     r.start_us, r.end_us, r.message_count, r.char_count, r.method, run_id)
                    for r in result.sender_runs
                ],
            )
            self.conn.executemany(
                """INSERT INTO conversation_session(
                       id, conversation_id, first_message_id, last_message_id,
                       start_at_utc_us, end_at_utc_us, message_count,
                       gap_threshold_us, method, processing_run_id
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (s.id, s.conversation_id, s.first_message_id, s.last_message_id,
                     s.start_us, s.end_us, s.message_count, s.gap_threshold_us, s.method, run_id)
                    for s in result.sessions
                ],
            )
            self.conn.executemany(
                """INSERT INTO conversation_thread(
                       id, conversation_id, session_id, method, confidence, processing_run_id
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                [(t.id, t.conversation_id, t.session_id, t.method, t.confidence, run_id) for t in result.threads],
            )
            self.conn.executemany(
                "INSERT INTO conversation_thread_message(thread_id, message_id, position) VALUES (?, ?, ?)",
                [
                    (t.id, message_id, position)
                    for t in result.threads
                    for position, message_id in enumerate(t.message_ids, start=1)
                ],
            )
            self.conn.executemany(
                """INSERT INTO processed_message(
                       message_id, processing_run_id, sequence_number, text_clean,
                       sender_run_id, session_id, thread_id,
                       char_count, word_count, line_count, emoji_count,
                       question_mark_count, exclamation_mark_count, uppercase_ratio,
                       has_question, has_url, has_attachment,
                       seconds_since_previous_message, seconds_since_previous_other_sender
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        m.message_id, run_id, m.sequence_number, m.text_clean,
                        m.sender_run_id, m.session_id, m.thread_id,
                        m.features.char_count, m.features.word_count, m.features.line_count,
                        m.features.emoji_count, m.features.question_mark_count,
                        m.features.exclamation_mark_count, m.features.uppercase_ratio,
                        int(m.features.has_question), int(m.features.has_url),
                        int(m.features.has_attachment), m.features.seconds_since_previous_message,
                        m.features.seconds_since_previous_other_sender,
                    )
                    for m in result.messages
                ],
            )
            self.conn.executemany(
                """INSERT INTO a3_duplicate_candidate(
                       message_id_a, message_id_b, classification, confidence, method, processing_run_id
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (d.left_message_id, d.right_message_id, d.classification, d.confidence, d.method, run_id)
                    for d in result.duplicate_candidates
                ],
            )
        return run_id
