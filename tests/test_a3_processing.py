from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.processing import (
    CanonicalMessage,
    MessageRelation,
    ProcessingConfig,
    ProcessingStore,
    load_a2_projection,
    process_messages,
)
from analyzazprav.processing.text import clean_text


MINIMAL_A2_SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE participant(id INTEGER PRIMARY KEY);
CREATE TABLE conversation(id INTEGER PRIMARY KEY);
CREATE TABLE message(id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL REFERENCES conversation(id), sender_id INTEGER REFERENCES participant(id), sent_at_utc_us INTEGER, text TEXT);
CREATE TABLE message_source(id INTEGER PRIMARY KEY, message_id INTEGER NOT NULL REFERENCES message(id), source_message_id TEXT, source_row_id TEXT);
CREATE TABLE attachment(id INTEGER PRIMARY KEY, sha256 TEXT, availability TEXT NOT NULL DEFAULT 'unknown');
CREATE TABLE message_attachment(message_id INTEGER NOT NULL REFERENCES message(id), attachment_id INTEGER NOT NULL REFERENCES attachment(id), position INTEGER, PRIMARY KEY(message_id, attachment_id));
CREATE TABLE message_relation(source_message_id INTEGER NOT NULL REFERENCES message(id), target_message_id INTEGER NOT NULL REFERENCES message(id), relation_type TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}');
CREATE VIEW analysis_messages AS SELECT id, conversation_id, sender_id, sent_at_utc_us, text FROM message;
CREATE VIEW analysis_attachments AS SELECT ma.message_id, a.id attachment_id, a.sha256, a.availability, ma.position FROM message_attachment ma JOIN attachment a ON a.id=ma.attachment_id;
"""


class TextCleaningTests(unittest.TestCase):
    def test_preserves_style_signals(self):
        original = "  AHOJ!!! ❤️\r\nCo???   \x00"
        self.assertEqual(clean_text(original), "AHOJ!!! ❤️\nCo???")
        self.assertEqual(original, "  AHOJ!!! ❤️\r\nCo???   \x00")


class ProcessingCoreTests(unittest.TestCase):
    def setUp(self):
        hour = 3_600_000_000
        self.messages = [
            CanonicalMessage(1, 10, 100, 0, "Ahoj", "guid-1", 1),
            CanonicalMessage(2, 10, 100, 1_000_000, "Jak se máš?", "guid-2", 2),
            CanonicalMessage(3, 10, 200, 2_000_000, "Dobře! 😊", "guid-3", 3),
            CanonicalMessage(4, 10, 200, 3_000_000, "Dobře! 😊", "different-export-id", 4),
            CanonicalMessage(5, 10, 100, 7 * hour, "Znovu", "guid-5", 5),
            CanonicalMessage(6, 10, 100, None, "Bez času", "guid-6", 6),
        ]

    def test_determinism_runs_sessions_and_unknown_time(self):
        first = process_messages(self.messages)
        second = process_messages(list(reversed(self.messages)))
        self.assertEqual(first, second)
        self.assertEqual([r.message_count for r in first.sender_runs], [2, 2, 2])
        self.assertEqual([s.message_count for s in first.sessions], [4, 1, 1])
        by_id = {m.message_id: m for m in first.messages}
        self.assertIsNone(by_id[6].features.seconds_since_previous_message)

    def test_duplicate_audit_flags_but_does_not_drop(self):
        result = process_messages(self.messages)
        self.assertEqual(len(result.messages), len(self.messages))
        self.assertEqual(len(result.duplicate_candidates), 1)
        self.assertEqual(result.duplicate_candidates[0].classification, "probable_cross_export")

    def test_only_explicit_reply_relations_form_threads(self):
        relations = [MessageRelation(3, 2, "reply"), MessageRelation(4, 3, "reaction")]
        result = process_messages(self.messages, relations)
        self.assertEqual(len(result.threads), 1)
        self.assertEqual(result.threads[0].message_ids, (2, 3))
        by_id = {m.message_id: m for m in result.messages}
        self.assertIsNotNone(by_id[2].thread_id)
        self.assertIsNone(by_id[4].thread_id)


class AdapterAndStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(Path(self.tmp.name) / "a3.sqlite")
        self.conn.executescript(MINIMAL_A2_SCHEMA)
        self.conn.executemany("INSERT INTO participant(id) VALUES (?)", [(1,), (2,)])
        self.conn.execute("INSERT INTO conversation(id) VALUES (10)")
        self.conn.executemany(
            "INSERT INTO message(id, conversation_id, sender_id, sent_at_utc_us, text) VALUES (?, 10, ?, ?, ?)",
            [(1, 1, 1_000_000, "Ahoj"), (2, 2, 2_000_000, "Ano"), (3, 1, None, "Bez času")],
        )
        self.conn.executemany(
            "INSERT INTO message_source(id, message_id, source_message_id, source_row_id) VALUES (?, ?, ?, ?)",
            [(1, 1, "g1", "1"), (2, 2, "g2", "2"), (3, 3, "g3", "3")],
        )
        self.conn.execute("INSERT INTO message_relation VALUES (2, 1, 'reply', '{}')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_adapter_pipeline_and_persistence(self):
        projection = load_a2_projection(self.conn)
        result = process_messages(list(projection.messages), list(projection.relations))
        store = ProcessingStore(self.conn, ROOT / "database" / "a3_schema.sql")
        store.initialize()
        run_id = store.replace_all(result, ProcessingConfig())
        self.assertGreater(run_id, 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM processed_message").fetchone()[0], 3)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM conversation_thread").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        raw = self.conn.execute("SELECT text FROM message WHERE id=1").fetchone()[0]
        self.assertEqual(raw, "Ahoj")


if __name__ == "__main__":
    unittest.main()
