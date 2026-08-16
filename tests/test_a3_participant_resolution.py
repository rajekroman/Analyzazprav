from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.processing import (
    CanonicalMessage,
    CanonicalParticipant,
    ParticipantIdentity,
    ProcessingConfig,
    ProcessingStore,
    process_messages,
)


class A3ParticipantResolutionTests(unittest.TestCase):
    def setUp(self):
        self.participants = [
            CanonicalParticipant(
                1,
                "Owner",
                True,
                (ParticipantIdentity(11, 1, "phone", "+420777111222", "+420 777 111 222"),),
            ),
            CanonicalParticipant(
                2,
                "Owner",
                True,
                (ParticipantIdentity(12, 2, "email", "owner@example.cz", "Owner@example.cz"),),
            ),
            CanonicalParticipant(
                3,
                "Alice",
                False,
                (ParticipantIdentity(13, 3, "phone", "+420777333444"),),
            ),
            CanonicalParticipant(
                4,
                " alice ",
                False,
                (ParticipantIdentity(14, 4, "email", "alice@example.cz"),),
            ),
        ]
        self.messages = [
            CanonicalMessage(101, 1001, 10, 1, 0, "A"),
            CanonicalMessage(102, 1002, 10, 2, 1_000_000, "B"),
            CanonicalMessage(103, 1003, 10, 3, 2_000_000, "C"),
        ]

    def test_explicit_self_aliases_merge_but_equal_names_do_not(self):
        first = process_messages(self.messages, participants=self.participants)
        second = process_messages(
            list(reversed(self.messages)),
            participants=list(reversed(self.participants)),
        )
        self.assertEqual(first, second)
        self.assertEqual(len(first.resolved_participants), 3)
        self.assertEqual(len(first.participant_aliases), 4)

        owner = next(participant for participant in first.resolved_participants if participant.is_self)
        self.assertEqual(owner.member_participant_ids, (1, 2))
        self.assertEqual(owner.method, "explicit_is_self_union_v1")
        self.assertEqual(owner.confidence, 1.0)

        self.assertEqual(len(first.participant_resolution_candidates), 1)
        candidate = first.participant_resolution_candidates[0]
        self.assertEqual((candidate.left_participant_id, candidate.right_participant_id), (3, 4))
        self.assertEqual(candidate.reason, "same_normalized_canonical_name")
        self.assertEqual(candidate.confidence, 0.35)

        self.assertEqual([run.message_count for run in first.sender_runs], [2, 1])
        self.assertEqual(first.sender_runs[0].resolved_participant_id, owner.id)
        self.assertIsNone(first.sender_runs[0].sender_id)

        by_membership = {message.membership_id: message for message in first.messages}
        self.assertEqual(by_membership[101].resolved_sender_id, owner.id)
        self.assertEqual(by_membership[102].resolved_sender_id, owner.id)
        self.assertIsNone(
            by_membership[102].features.seconds_since_previous_other_sender,
            "switching between aliases of the same resolved person is not an opposite-sender response",
        )
        self.assertEqual(by_membership[103].features.seconds_since_previous_other_sender, 1.0)

    def test_resolution_persists_without_mutating_a2_participants(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(Path(tmp) / "a3.sqlite")
            conn.executescript(
                """
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
                    conversation_id INTEGER NOT NULL REFERENCES conversation(id)
                );
                """
            )
            conn.executemany(
                "INSERT INTO participant(id, canonical_name, is_self) VALUES (?, ?, ?)",
                [(1, "Owner", 1), (2, "Owner", 1), (3, "Alice", 0), (4, " alice ", 0)],
            )
            conn.executemany(
                """INSERT INTO participant_identity(
                       id, participant_id, identity_type, normalized_value, original_value
                   ) VALUES (?, ?, ?, ?, ?)""",
                [
                    (11, 1, "phone", "+420777111222", "+420 777 111 222"),
                    (12, 2, "email", "owner@example.cz", "Owner@example.cz"),
                    (13, 3, "phone", "+420777333444", None),
                    (14, 4, "email", "alice@example.cz", None),
                ],
            )
            conn.execute("INSERT INTO conversation(id) VALUES (10)")
            conn.executemany("INSERT INTO message(id) VALUES (?)", [(1001,), (1002,), (1003,)])
            conn.executemany(
                "INSERT INTO message_conversation(id, message_id, conversation_id) VALUES (?, ?, 10)",
                [(101, 1001), (102, 1002), (103, 1003)],
            )
            conn.commit()

            before = {
                "participant": conn.execute("SELECT * FROM participant ORDER BY id").fetchall(),
                "identity": conn.execute("SELECT * FROM participant_identity ORDER BY id").fetchall(),
            }
            result = process_messages(self.messages, participants=self.participants)
            store = ProcessingStore(conn, ROOT / "database" / "a3_schema.sql")
            store.initialize()
            run_id = store.persist(result, ProcessingConfig())

            self.assertEqual(conn.execute("SELECT COUNT(*) FROM resolved_participant WHERE processing_run_id=?", (run_id,)).fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM resolved_participant_member WHERE processing_run_id=?", (run_id,)).fetchone()[0], 4)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM participant_alias WHERE processing_run_id=?", (run_id,)).fetchone()[0], 4)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM participant_resolution_candidate WHERE processing_run_id=?", (run_id,)).fetchone()[0], 1)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(before["participant"], conn.execute("SELECT * FROM participant ORDER BY id").fetchall())
            self.assertEqual(before["identity"], conn.execute("SELECT * FROM participant_identity ORDER BY id").fetchall())
            conn.close()


if __name__ == "__main__":
    unittest.main()
