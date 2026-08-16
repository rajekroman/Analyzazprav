from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.processing import (
    MessageRelation,
    ProcessingConfig,
    ProcessingStore,
    load_a2_projection,
    process_messages,
)


A2_V5_FIXTURE = """
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
    is_primary INTEGER NOT NULL DEFAULT 0,
    UNIQUE(message_id, conversation_id)
);
CREATE TABLE message_source(
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES message(id),
    source_message_id TEXT,
    source_row_id TEXT
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
    id INTEGER PRIMARY KEY,
    source_message_id INTEGER NOT NULL REFERENCES message(id),
    target_message_id INTEGER NOT NULL REFERENCES message(id),
    relation_type TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE VIEW analysis_messages AS
SELECT mc.id AS membership_id,
       m.id,
       mc.conversation_id,
       m.sender_id,
       m.sent_at_utc_us,
       m.timezone_offset_min,
       m.message_type,
       m.text
FROM message_conversation mc
JOIN message m ON m.id=mc.message_id;
CREATE VIEW analysis_attachments AS
SELECT mao.id AS occurrence_id,
       mao.message_id,
       a.id AS attachment_id,
       a.sha256,
       a.mime_type,
       a.size_bytes,
       a.filename,
       a.availability,
       mao.position
FROM message_attachment_occurrence mao
JOIN attachment a ON a.id=mao.attachment_id;
"""


class A3MembershipAndParticipantContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(Path(self.tmp.name) / "contract.sqlite")
        self.conn.executescript(A2_V5_FIXTURE)
        self.conn.executemany(
            "INSERT INTO participant(id, canonical_name, is_self) VALUES (?, ?, ?)",
            [
                (1, "Owner", 1),
                (2, "Owner", 1),
                (3, "Alice", 0),
                (4, " alice ", 0),
            ],
        )
        self.conn.executemany(
            """INSERT INTO participant_identity(
                   id, participant_id, identity_type, normalized_value, original_value
               ) VALUES (?, ?, ?, ?, ?)""",
            [
                (11, 1, "phone", "+420777111222", "+420 777 111 222"),
                (12, 2, "email", "owner@example.cz", "Owner@example.cz"),
                (13, 3, "phone", "+420777333444", "+420 777 333 444"),
                (14, 4, "email", "alice@example.cz", "alice@example.cz"),
            ],
        )
        self.conn.executemany("INSERT INTO conversation(id) VALUES (?)", [(10,), (20,)])
        self.conn.executemany(
            """INSERT INTO message(
                   id, conversation_id, sender_id, sent_at_utc_us,
                   timezone_offset_min, message_type, text
               ) VALUES (?, ?, ?, ?, 60, 'text', ?)""",
            [
                (101, 10, 1, 0, "První"),
                (102, 10, 2, 1_000_000, "Druhá"),
                (103, 10, 3, 2_000_000, "Alice"),
            ],
        )
        self.conn.executemany(
            """INSERT INTO message_conversation(
                   id, message_id, conversation_id, is_primary
               ) VALUES (?, ?, ?, ?)""",
            [
                (1001, 101, 10, 1),
                (1002, 101, 20, 0),
                (1003, 102, 10, 1),
                (1004, 103, 10, 1),
            ],
        )
        self.conn.executemany(
            "INSERT INTO message_source(id, message_id, source_message_id, source_row_id) VALUES (?, ?, ?, ?)",
            [
                (1, 101, "m101", "1"),
                (2, 102, "m102", "2"),
                (3, 103, "m103", "3"),
            ],
        )
        self.conn.execute(
            """INSERT INTO message_relation(
                   id, source_message_id, target_message_id, relation_type, metadata_json
               ) VALUES (1, 102, 101, 'reply', '{}')"""
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_multi_conversation_membership_and_participant_resolution_are_lossless(self):
        projection = load_a2_projection(self.conn)
        self.assertEqual(
            [(m.id, m.conversation_id, m.membership_id) for m in projection.messages],
            [
                (101, 10, 1001),
                (102, 10, 1003),
                (103, 10, 1004),
                (101, 20, 1002),
            ],
        )

        result = process_messages(
            list(projection.messages),
            list(projection.relations),
            participants=list(projection.participants),
        )
        reversed_result = process_messages(
            list(reversed(projection.messages)),
            list(projection.relations),
            participants=list(reversed(projection.participants)),
        )
        self.assertEqual(result, reversed_result)

        self.assertEqual(len(result.messages), 4)
        self.assertEqual(len({m.message_id for m in result.messages}), 3)
        self.assertEqual(len(result.resolved_participants), 3)
        self.assertEqual(len(result.participant_aliases), 4)
        self.assertEqual(len(result.participant_resolution_candidates), 1)
        candidate = result.participant_resolution_candidates[0]
        self.assertEqual((candidate.left_participant_id, candidate.right_participant_id), (3, 4))
        self.assertEqual(candidate.reason, "same_normalized_canonical_name")

        owner = next(p for p in result.resolved_participants if p.is_self)
        self.assertEqual(owner.member_participant_ids, (1, 2))
        self.assertEqual(owner.confidence, 1.0)

        conv10_runs = [run for run in result.sender_runs if run.conversation_id == 10]
        self.assertEqual([run.message_count for run in conv10_runs], [2, 1])
        self.assertEqual(conv10_runs[0].resolved_participant_id, owner.id)
        self.assertIsNone(conv10_runs[0].sender_id)

        self.assertEqual(len(result.threads), 1)
        thread = result.threads[0]
        self.assertEqual(thread.conversation_id, 10)
        self.assertEqual(thread.message_ids, (101, 102))
        self.assertEqual(thread.membership_ids, (1001, 1003))

        before = {
            table: self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "participant",
                "participant_identity",
                "message",
                "message_conversation",
                "message_source",
            )
        }

        store = ProcessingStore(self.conn, ROOT / "database" / "a3_schema.sql")
        store.initialize()
        run_id = store.replace_all(result, ProcessingConfig())
        self.assertGreater(run_id, 0)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM processed_message").fetchone()[0],
            4,
        )
        self.assertEqual(
            self.conn.execute(
                """SELECT input_message_count, input_membership_count,
                          output_message_count, output_membership_count
                   FROM processing_run WHERE id=?""",
                (run_id,),
            ).fetchone(),
            (3, 4, 3, 4),
        )
        memberships = self.conn.execute(
            """SELECT message_id, conversation_id, membership_id
               FROM processed_message
               ORDER BY conversation_id, sequence_number"""
        ).fetchall()
        self.assertEqual(
            memberships,
            [(101, 10, 1001), (102, 10, 1003), (103, 10, 1004), (101, 20, 1002)],
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM participant_alias").fetchone()[0],
            4,
        )
        self.assertEqual(
            self.conn.execute("PRAGMA foreign_key_check").fetchall(),
            [],
        )
        after = {
            table: self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before
        }
        self.assertEqual(before, after)

    def test_ambiguous_reply_membership_is_not_guessed(self):
        self.conn.execute(
            """INSERT INTO message(
                   id, conversation_id, sender_id, sent_at_utc_us,
                   timezone_offset_min, message_type, text
               ) VALUES (104, 10, 3, 3000000, 60, 'text', 'Další')"""
        )
        self.conn.executemany(
            """INSERT INTO message_conversation(
                   id, message_id, conversation_id, is_primary
               ) VALUES (?, 104, ?, ?)""",
            [(1005, 10, 1), (1006, 20, 0)],
        )
        self.conn.execute(
            "INSERT INTO message_source(id, message_id, source_message_id, source_row_id) VALUES (4, 104, 'm104', '4')"
        )
        self.conn.execute(
            """INSERT INTO message_relation(
                   id, source_message_id, target_message_id, relation_type, metadata_json
               ) VALUES (2, 104, 101, 'reply', '{}')"""
        )
        self.conn.commit()

        projection = load_a2_projection(self.conn)
        ambiguous = process_messages(
            list(projection.messages),
            [MessageRelation(104, 101, "reply", {})],
            participants=list(projection.participants),
        )
        self.assertEqual(ambiguous.threads, ())

        disambiguated = process_messages(
            list(projection.messages),
            [MessageRelation(104, 101, "reply", {"conversation_id": 10})],
            participants=list(projection.participants),
        )
        self.assertEqual(len(disambiguated.threads), 1)
        self.assertEqual(disambiguated.threads[0].conversation_id, 10)
        self.assertEqual(disambiguated.threads[0].message_ids, (101, 104))
        self.assertEqual(disambiguated.threads[0].membership_ids, (1001, 1005))
        conv20 = [m for m in disambiguated.messages if m.conversation_id == 20]
        self.assertTrue(all(message.thread_id is None for message in conv20))


if __name__ == "__main__":
    unittest.main()
