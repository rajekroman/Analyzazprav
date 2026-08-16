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
from analyzazprav.processing.media import classify_media
from analyzazprav.processing.text import clean_text


MINIMAL_A2_SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE participant(id INTEGER PRIMARY KEY);
CREATE TABLE conversation(id INTEGER PRIMARY KEY);
CREATE TABLE message(
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversation(id),
    sender_id INTEGER REFERENCES participant(id),
    sent_at_utc_us INTEGER,
    timezone_offset_min INTEGER,
    message_type TEXT NOT NULL DEFAULT 'text',
    text TEXT
);
CREATE TABLE message_conversation(
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES message(id),
    conversation_id INTEGER NOT NULL REFERENCES conversation(id),
    UNIQUE(message_id, conversation_id)
);
CREATE TABLE message_source(
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES message(id),
    source_message_id TEXT,
    source_row_id TEXT,
    source_record_key TEXT
);
CREATE TABLE attachment(
    id INTEGER PRIMARY KEY,
    sha256 TEXT,
    mime_type TEXT,
    size_bytes INTEGER,
    filename TEXT,
    availability TEXT NOT NULL DEFAULT 'unknown'
);
CREATE TABLE message_attachment_occurrence(
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES message(id),
    attachment_id INTEGER NOT NULL REFERENCES attachment(id),
    position INTEGER
);
CREATE TABLE message_relation(
    source_message_id INTEGER NOT NULL REFERENCES message(id),
    target_message_id INTEGER NOT NULL REFERENCES message(id),
    relation_type TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE VIEW analysis_messages AS
SELECT mc.id AS membership_id, m.id, mc.conversation_id, m.sender_id,
       m.sent_at_utc_us, m.timezone_offset_min, m.message_type, m.text
FROM message_conversation mc JOIN message m ON m.id=mc.message_id;
CREATE VIEW analysis_attachments AS
SELECT mao.id AS occurrence_id, mao.message_id, a.id attachment_id, a.sha256,
       a.mime_type, a.size_bytes, a.filename, a.availability, mao.position
FROM message_attachment_occurrence mao JOIN attachment a ON a.id=mao.attachment_id;
"""


def msg(
    membership_id: int,
    message_id: int,
    conversation_id: int,
    sender_id: int | None,
    timestamp_us: int | None,
    text: str,
    source_message_id: str,
    source_order: int,
) -> CanonicalMessage:
    return CanonicalMessage(
        membership_id=membership_id,
        id=message_id,
        conversation_id=conversation_id,
        sender_id=sender_id,
        timestamp_us=timestamp_us,
        text=text,
        source_message_id=source_message_id,
        source_record_keys=(f"rk-{message_id}",),
        source_order=source_order,
    )


class TextCleaningTests(unittest.TestCase):
    def test_preserves_style_signals(self):
        original = "  AHOJ!!! ❤️\r\nCo???   \x00"
        self.assertEqual(clean_text(original), "AHOJ!!! ❤️\nCo???")
        self.assertEqual(original, "  AHOJ!!! ❤️\r\nCo???   \x00")

    def test_media_classification(self):
        self.assertEqual(classify_media("image/jpeg", "x.bin"), "image")
        self.assertEqual(classify_media(None, "clip.mov"), "video")
        self.assertEqual(classify_media("application/pdf", "x"), "document")
        self.assertEqual(classify_media("image/gif", "x.gif"), "gif")


class ProcessingCoreTests(unittest.TestCase):
    def setUp(self):
        hour = 3_600_000_000
        self.messages = [
            msg(1, 1, 10, 100, 0, "Ahoj", "guid-1", 1),
            msg(2, 2, 10, 100, 1_000_000, "Jak se máš?", "guid-2", 2),
            msg(3, 3, 10, 200, 2_000_000, "Dobře! 😊", "guid-3", 3),
            msg(4, 4, 10, 200, 3_000_000, "Dobře! 😊", "different-export-id", 4),
            msg(5, 5, 10, 100, 7 * hour, "Znovu", "guid-5", 5),
            msg(6, 6, 10, 100, None, "Bez času", "guid-6", 6),
        ]

    def test_determinism_runs_sessions_and_unknown_time(self):
        first = process_messages(self.messages)
        second = process_messages(list(reversed(self.messages)))
        self.assertEqual(first, second)
        self.assertEqual([r.message_count for r in first.sender_runs], [2, 2, 2])
        self.assertEqual([s.message_count for s in first.sessions], [4, 1, 1])
        by_id = {m.message_id: m for m in first.messages}
        self.assertIsNone(by_id[6].features.seconds_since_previous_message)
        self.assertIsNone(by_id[6].features.utc_year)

    def test_duplicate_audit_flags_but_does_not_drop(self):
        result = process_messages(self.messages)
        self.assertEqual(len(result.messages), len(self.messages))
        self.assertEqual(len(result.duplicate_candidates), 1)
        self.assertEqual(result.duplicate_candidates[0].classification, "probable_cross_export")

    def test_duplicate_candidates_use_stable_id_order_and_scale_by_adjacency(self):
        reversed_ids = [
            msg(20, 20, 10, 100, 0, "Stejné", "a", 1),
            msg(10, 10, 10, 100, 1_000_000, "Stejné", "b", 2),
        ]
        candidate = process_messages(reversed_ids).duplicate_candidates[0]
        self.assertEqual((candidate.left_message_id, candidate.right_message_id), (10, 20))

        repeated = [
            msg(1_000 + index, 1_000 + index, 10, 100, index * 3_000_000, "Ano", f"r-{index}", index)
            for index in range(3_000)
        ]
        self.assertEqual(process_messages(repeated).duplicate_candidates, ())

    def test_only_explicit_reply_relations_form_threads(self):
        relations = [MessageRelation(3, 2, "reply"), MessageRelation(4, 3, "reaction")]
        result = process_messages(self.messages, relations)
        self.assertEqual(len(result.threads), 1)
        self.assertEqual(result.threads[0].message_ids, (2, 3))
        self.assertEqual(result.threads[0].membership_ids, (2, 3))
        by_id = {m.message_id: m for m in result.messages}
        self.assertIsNotNone(by_id[2].thread_id)
        self.assertIsNone(by_id[4].thread_id)

    def test_explicit_reply_can_cross_session_boundary(self):
        result = process_messages(self.messages, [MessageRelation(5, 1, "reply")])
        self.assertEqual(len(result.threads), 1)
        self.assertEqual(result.threads[0].message_ids, (1, 5))
        self.assertIsNone(result.threads[0].session_id)
        by_id = {m.message_id: m for m in result.messages}
        self.assertEqual(by_id[1].thread_id, by_id[5].thread_id)
        self.assertNotEqual(by_id[1].session_id, by_id[5].session_id)

    def test_relation_resolves_only_shared_conversation_memberships(self):
        # Canonical message 1 also belongs to chat 99. Message 100 exists only in
        # chat 99. The explicit relation must create a thread only there and must
        # not contaminate message 1's chat-10 membership.
        shared_other = msg(101, 1, 99, 100, 0, "Ahoj", "guid-1", 1)
        foreign = msg(102, 100, 99, 300, 4_000_000, "Jiný chat", "guid-100", 2)
        result = process_messages(
            self.messages + [shared_other, foreign],
            [MessageRelation(100, 1, "reply")],
        )
        self.assertEqual(len(result.threads), 1)
        self.assertEqual(result.threads[0].conversation_id, 99)
        self.assertEqual(set(result.threads[0].membership_ids), {101, 102})
        by_membership = {m.membership_id: m for m in result.messages}
        self.assertIsNone(by_membership[1].thread_id)
        self.assertIsNotNone(by_membership[101].thread_id)

    def test_same_canonical_message_in_two_memberships_is_processed_twice(self):
        first = msg(501, 42, 10, 100, 1_000_000, "Stejná zpráva", "g-42", 1)
        second = msg(502, 42, 20, 100, 1_000_000, "Stejná zpráva", "g-42", 1)
        result = process_messages([first, second])
        self.assertEqual(len(result.messages), 2)
        self.assertEqual({m.message_id for m in result.messages}, {42})
        self.assertEqual({m.membership_id for m in result.messages}, {501, 502})
        self.assertEqual({m.conversation_id for m in result.messages}, {10, 20})
        self.assertEqual(len(result.sessions), 2)


class AdapterAndStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(Path(self.tmp.name) / "a3.sqlite")
        self.conn.executescript(MINIMAL_A2_SCHEMA)
        self.conn.executemany("INSERT INTO participant(id) VALUES (?)", [(1,), (2,)])
        self.conn.execute("INSERT INTO conversation(id) VALUES (10)")
        self.conn.executemany(
            """INSERT INTO message(
                   id, conversation_id, sender_id, sent_at_utc_us,
                   timezone_offset_min, message_type, text
               ) VALUES (?, 10, ?, ?, ?, 'text', ?)""",
            [(1, 1, 0, 60, "Ahoj"), (2, 2, 1_000_000, 60, "Ano"), (3, 1, None, None, "Bez času")],
        )
        self.conn.executemany(
            "INSERT INTO message_conversation(id, message_id, conversation_id) VALUES (?, ?, 10)",
            [(101, 1), (102, 2), (103, 3)],
        )
        self.conn.executemany(
            """INSERT INTO message_source(
                   id, message_id, source_message_id, source_row_id, source_record_key
               ) VALUES (?, ?, ?, ?, ?)""",
            [(1, 1, "g1", "1", "rk1"), (2, 2, "g2", "2", "rk2"), (3, 3, "g3", "3", "rk3")],
        )
        self.conn.executemany(
            "INSERT INTO attachment(id, sha256, mime_type, size_bytes, filename, availability) VALUES (?, ?, ?, ?, ?, ?)",
            [(1, "abc", "image/jpeg", 100, "photo.jpg", "available"), (2, None, "application/pdf", 200, "doc.pdf", "missing")],
        )
        self.conn.executemany(
            """INSERT INTO message_attachment_occurrence(
                   id, message_id, attachment_id, position
               ) VALUES (?, ?, ?, ?)""",
            [(201, 1, 1, 1), (202, 2, 2, 1)],
        )
        self.conn.execute("INSERT INTO message_relation VALUES (2, 1, 'reply', '{}')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_initialize_rebuilds_only_outdated_a3_schema(self):
        self.conn.executescript(
            """
            CREATE TABLE processing_run(id INTEGER PRIMARY KEY);
            CREATE TABLE processed_message(
                message_id INTEGER PRIMARY KEY REFERENCES message(id) ON DELETE CASCADE,
                processing_run_id INTEGER REFERENCES processing_run(id),
                text_clean TEXT
            );
            CREATE TABLE conversation_thread(
                id INTEGER PRIMARY KEY,
                session_id INTEGER NOT NULL
            );
            """
        )
        original_message_count = self.conn.execute("SELECT COUNT(*) FROM message").fetchone()[0]
        store = ProcessingStore(self.conn, ROOT / "database" / "a3_schema.sql")
        store.initialize()
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(processed_message)")}
        self.assertIn("membership_id", columns)
        self.assertIn("conversation_id", columns)
        self.assertIn("image_count", columns)
        self.assertIn("local_hour", columns)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM message").fetchone()[0], original_message_count)
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_adapter_pipeline_media_calendar_and_versioned_persistence(self):
        projection = load_a2_projection(self.conn)
        self.assertEqual([m.membership_id for m in projection.messages], [101, 102, 103])
        self.assertEqual(projection.messages[0].source_record_keys, ("rk1",))

        result = process_messages(list(projection.messages), list(projection.relations))
        by_id = {m.message_id: m for m in result.messages}
        self.assertEqual(by_id[1].features.image_count, 1)
        self.assertEqual(by_id[1].features.utc_hour, 0)
        self.assertEqual(by_id[1].features.local_hour, 1)
        self.assertEqual(by_id[2].features.document_count, 1)
        self.assertEqual(by_id[2].features.missing_attachment_count, 1)

        store = ProcessingStore(self.conn, ROOT / "database" / "a3_schema.sql")
        store.initialize()
        first_run = store.persist(result, ProcessingConfig())
        second_run = store.persist(result, ProcessingConfig(session_gap_seconds=3 * 60 * 60))
        self.assertNotEqual(first_run, second_run)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM processing_run").fetchone()[0], 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM processed_message").fetchone()[0], 6)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM processed_message WHERE processing_run_id=?",
                (first_run,),
            ).fetchone()[0],
            3,
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM analysis_processed_messages_latest").fetchone()[0],
            3,
        )
        persisted = self.conn.execute(
            """SELECT image_count, local_hour
               FROM processed_message
               WHERE processing_run_id=? AND membership_id=101""",
            (first_run,),
        ).fetchone()
        self.assertEqual(persisted, (1, 1))
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.conn.execute("SELECT text FROM message WHERE id=1").fetchone()[0], "Ahoj")


if __name__ == "__main__":
    unittest.main()
