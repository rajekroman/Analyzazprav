from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.processing import ProcessingStore


MINIMAL_A2_SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE participant(
    id INTEGER PRIMARY KEY,
    canonical_name TEXT,
    is_self INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE participant_identity(
    id INTEGER PRIMARY KEY,
    participant_id INTEGER NOT NULL REFERENCES participant(id),
    identity_type TEXT NOT NULL,
    normalized_value TEXT NOT NULL,
    original_value TEXT
);
CREATE TABLE conversation(id INTEGER PRIMARY KEY);
CREATE TABLE message(id INTEGER PRIMARY KEY);
CREATE TABLE message_conversation(
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES message(id),
    conversation_id INTEGER NOT NULL REFERENCES conversation(id),
    UNIQUE(message_id, conversation_id)
);
"""


LEGACY_V4_DERIVED_SCHEMA = """
CREATE TABLE processing_run (
    id INTEGER PRIMARY KEY,
    processing_version TEXT NOT NULL,
    started_at_utc_us INTEGER NOT NULL,
    finished_at_utc_us INTEGER,
    status TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    input_membership_count INTEGER NOT NULL DEFAULT 0,
    canonical_message_count INTEGER NOT NULL DEFAULT 0,
    output_membership_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE processed_message (
    processing_run_id INTEGER NOT NULL,
    membership_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    conversation_id INTEGER NOT NULL,
    sequence_number INTEGER NOT NULL DEFAULT 1,
    text_clean TEXT,
    sender_run_id INTEGER NOT NULL DEFAULT 1,
    session_id INTEGER NOT NULL DEFAULT 1,
    thread_id INTEGER,
    char_count INTEGER NOT NULL DEFAULT 0,
    word_count INTEGER NOT NULL DEFAULT 0,
    line_count INTEGER NOT NULL DEFAULT 0,
    emoji_count INTEGER NOT NULL DEFAULT 0,
    question_mark_count INTEGER NOT NULL DEFAULT 0,
    exclamation_mark_count INTEGER NOT NULL DEFAULT 0,
    uppercase_ratio REAL NOT NULL DEFAULT 0,
    has_question INTEGER NOT NULL DEFAULT 0,
    has_url INTEGER NOT NULL DEFAULT 0,
    has_attachment INTEGER NOT NULL DEFAULT 0,
    attachment_count INTEGER NOT NULL DEFAULT 0,
    image_count INTEGER NOT NULL DEFAULT 0,
    gif_count INTEGER NOT NULL DEFAULT 0,
    video_count INTEGER NOT NULL DEFAULT 0,
    audio_count INTEGER NOT NULL DEFAULT 0,
    document_count INTEGER NOT NULL DEFAULT 0,
    other_media_count INTEGER NOT NULL DEFAULT 0,
    missing_attachment_count INTEGER NOT NULL DEFAULT 0,
    seconds_since_previous_message REAL,
    seconds_since_previous_other_sender REAL,
    utc_year INTEGER,
    utc_month INTEGER,
    utc_day INTEGER,
    utc_weekday INTEGER,
    utc_hour INTEGER,
    local_year INTEGER,
    local_month INTEGER,
    local_day INTEGER,
    local_weekday INTEGER,
    local_hour INTEGER,
    PRIMARY KEY(processing_run_id, membership_id)
);
CREATE VIEW analysis_processed_messages_latest AS
SELECT pm.*
FROM processed_message pm
JOIN (
    SELECT MAX(id) AS processing_run_id
    FROM processing_run
    WHERE status='completed'
) latest ON latest.processing_run_id = pm.processing_run_id;
"""


class A3V5UpgradeTests(unittest.TestCase):
    def _connection(self) -> tuple[tempfile.TemporaryDirectory, sqlite3.Connection]:
        tmp = tempfile.TemporaryDirectory()
        conn = sqlite3.connect(Path(tmp.name) / "messages.sqlite")
        conn.executescript(MINIMAL_A2_SCHEMA)
        return tmp, conn

    def test_integrated_v4_history_survives_v5_sidecar_initialization(self):
        tmp, conn = self._connection()
        try:
            conn.executescript(LEGACY_V4_DERIVED_SCHEMA)
            conn.execute("INSERT INTO participant(id, canonical_name) VALUES (1, 'Alice')")
            conn.execute("INSERT INTO conversation(id) VALUES (10)")
            conn.execute("INSERT INTO message(id) VALUES (1001)")
            conn.execute(
                "INSERT INTO message_conversation(id, message_id, conversation_id) VALUES (101, 1001, 10)"
            )
            conn.execute(
                """INSERT INTO processing_run(
                       id, processing_version, started_at_utc_us, finished_at_utc_us,
                       status, config_json, input_membership_count,
                       canonical_message_count, output_membership_count
                   ) VALUES (7, '4', 1, 2, 'completed', '{}', 1, 1, 1)"""
            )
            conn.execute(
                """INSERT INTO processed_message(
                       processing_run_id, membership_id, message_id, conversation_id,
                       text_clean, utc_year, utc_month, utc_day
                   ) VALUES (7, 101, 1001, 10, 'historical', 2026, 8, 16)"""
            )
            conn.commit()

            before_run = conn.execute(
                "SELECT * FROM processing_run WHERE id=7"
            ).fetchone()
            before_message = conn.execute(
                "SELECT * FROM processed_message WHERE processing_run_id=7 AND membership_id=101"
            ).fetchone()

            store = ProcessingStore(conn, ROOT / "database" / "a3_schema.sql")
            store.initialize()
            store.initialize()

            self.assertEqual(
                before_run,
                conn.execute("SELECT * FROM processing_run WHERE id=7").fetchone(),
            )
            self.assertEqual(
                before_message,
                conn.execute(
                    "SELECT * FROM processed_message WHERE processing_run_id=7 AND membership_id=101"
                ).fetchone(),
            )
            self.assertIsNotNone(
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='resolved_participant'"
                ).fetchone()
            )
            self.assertIsNotNone(
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='processed_message_resolved_sender'"
                ).fetchone()
            )
            self.assertEqual(
                conn.execute(
                    """SELECT resolved_sender_id
                       FROM analysis_processed_messages_resolved_latest
                       WHERE processing_run_id=7 AND membership_id=101"""
                ).fetchone()[0],
                None,
            )
            index_names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='processed_message'"
                )
            }
            self.assertIn("idx_processed_message_utc_period", index_names)
            self.assertIn("idx_processed_message_local_period", index_names)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            conn.close()
            tmp.cleanup()

    def test_alias_identity_must_belong_to_the_recorded_participant(self):
        tmp, conn = self._connection()
        try:
            conn.executemany(
                "INSERT INTO participant(id, canonical_name) VALUES (?, ?)",
                [(1, "Alice"), (2, "Bob")],
            )
            conn.execute(
                """INSERT INTO participant_identity(
                       id, participant_id, identity_type, normalized_value
                   ) VALUES (11, 1, 'phone', '+420777111222')"""
            )
            conn.commit()

            store = ProcessingStore(conn, ROOT / "database" / "a3_schema.sql")
            store.initialize()
            conn.execute(
                """INSERT INTO processing_run(
                       id, processing_version, started_at_utc_us, finished_at_utc_us,
                       status, config_json
                   ) VALUES (1, '5', 1, 2, 'completed', '{}')"""
            )
            conn.execute(
                """INSERT INTO resolved_participant(
                       processing_run_id, id, canonical_name, is_self, method, confidence
                   ) VALUES (1, 2, 'Bob', 0, 'a2_participant_membership_v1', 1.0)"""
            )

            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """INSERT INTO participant_alias(
                           processing_run_id, resolved_participant_id, participant_id,
                           participant_identity_id, identity_type, normalized_value,
                           method, confidence
                       ) VALUES (
                           1, 2, 2, 11, 'phone', '+420777111222',
                           'a2_identity_membership_v1', 1.0
                       )"""
                )
        finally:
            conn.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
