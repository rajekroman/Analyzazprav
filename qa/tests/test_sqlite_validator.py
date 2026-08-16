from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from qa.sqlite_validator import STATUS_FAIL, STATUS_PASS, validate_sqlite_database


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE import_run (
 id INTEGER PRIMARY KEY,
 status TEXT NOT NULL,
 finished_at_utc_us INTEGER
);
CREATE TABLE participant (id INTEGER PRIMARY KEY);
CREATE TABLE participant_identity (
 id INTEGER PRIMARY KEY,
 participant_id INTEGER REFERENCES participant(id)
);
CREATE TABLE conversation (id INTEGER PRIMARY KEY);
CREATE TABLE conversation_source (
 id INTEGER PRIMARY KEY,
 conversation_id INTEGER REFERENCES conversation(id),
 import_run_id INTEGER REFERENCES import_run(id)
);
CREATE TABLE conversation_participant (
 conversation_id INTEGER REFERENCES conversation(id),
 participant_id INTEGER REFERENCES participant(id)
);
CREATE TABLE message (
 id INTEGER PRIMARY KEY,
 conversation_id INTEGER REFERENCES conversation(id),
 created_import_id INTEGER REFERENCES import_run(id)
);
CREATE TABLE message_source (
 id INTEGER PRIMARY KEY,
 message_id INTEGER REFERENCES message(id),
 import_run_id INTEGER REFERENCES import_run(id),
 source_hash TEXT NOT NULL
);
CREATE TABLE duplicate_candidate (
 id INTEGER PRIMARY KEY,
 message_id_a INTEGER REFERENCES message(id),
 message_id_b INTEGER REFERENCES message(id)
);
CREATE TABLE message_relation (
 id INTEGER PRIMARY KEY,
 source_message_id INTEGER REFERENCES message(id),
 target_message_id INTEGER REFERENCES message(id)
);
CREATE TABLE attachment (id INTEGER PRIMARY KEY);
CREATE TABLE message_attachment (
 message_id INTEGER REFERENCES message(id),
 attachment_id INTEGER REFERENCES attachment(id)
);
CREATE TABLE attachment_source (
 id INTEGER PRIMARY KEY,
 attachment_id INTEGER REFERENCES attachment(id),
 import_run_id INTEGER REFERENCES import_run(id)
);
CREATE VIEW analysis_messages AS SELECT id FROM message;
CREATE VIEW analysis_conversations AS SELECT id FROM conversation;
CREATE VIEW analysis_attachments AS SELECT message_id, attachment_id FROM message_attachment;
"""


def create_valid_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO import_run(id,status,finished_at_utc_us) VALUES (1,'completed',1)")
    conn.execute("INSERT INTO participant(id) VALUES (1)")
    conn.execute("INSERT INTO conversation(id) VALUES (1)")
    conn.execute("INSERT INTO conversation_source(id,conversation_id,import_run_id) VALUES (1,1,1)")
    conn.execute("INSERT INTO conversation_participant(conversation_id,participant_id) VALUES (1,1)")
    conn.execute("INSERT INTO message(id,conversation_id,created_import_id) VALUES (1,1,1)")
    conn.execute("INSERT INTO message_source(id,message_id,import_run_id,source_hash) VALUES (1,1,1,'hash')")
    conn.execute("INSERT INTO attachment(id) VALUES (1)")
    conn.execute("INSERT INTO message_attachment(message_id,attachment_id) VALUES (1,1)")
    conn.execute("INSERT INTO attachment_source(id,attachment_id,import_run_id) VALUES (1,1,1)")
    conn.commit()
    conn.close()


class SQLiteValidatorTests(unittest.TestCase):
    def test_valid_a2_shape_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "messages.sqlite"
            create_valid_database(path)
            report = validate_sqlite_database(path)
        self.assertEqual(STATUS_PASS, report["status"])
        self.assertEqual(1, report["counts"]["message"])
        self.assertEqual(0, report["checks"]["messages_without_source"])

    def test_message_without_source_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "messages.sqlite"
            create_valid_database(path)
            conn = sqlite3.connect(path)
            conn.execute("DELETE FROM message_source")
            conn.commit()
            conn.close()
            report = validate_sqlite_database(path)
        self.assertEqual(STATUS_FAIL, report["status"])
        self.assertIn("MESSAGE_SOURCE_TRACE_MISSING", {i["code"] for i in report["issues"]})

    def test_missing_required_schema_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "messages.sqlite"
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE message(id INTEGER PRIMARY KEY)")
            conn.commit()
            conn.close()
            report = validate_sqlite_database(path)
        self.assertEqual(STATUS_FAIL, report["status"])
        self.assertIn("A2_REQUIRED_TABLES_MISSING", {i["code"] for i in report["issues"]})


if __name__ == "__main__":
    unittest.main()
