from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from analyzazprav.processing import (
    CanonicalMessage,
    CanonicalParticipant,
    ParticipantIdentity,
    ProcessingConfig,
    ProcessingStore,
    process_messages,
)
from analyzazprav.qa.participant_resolution import validate_participant_resolution

ROOT = Path(__file__).resolve().parents[1]


class A7ParticipantResolutionOracleTests(unittest.TestCase):
    def _build_database(self, root: Path) -> tuple[Path, int]:
        path = root / "participant-resolution.sqlite"
        conn = sqlite3.connect(path)
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
            CREATE TABLE message(
                id INTEGER PRIMARY KEY,
                sender_id INTEGER REFERENCES participant(id)
            );
            CREATE TABLE message_conversation(
                id INTEGER PRIMARY KEY,
                message_id INTEGER NOT NULL REFERENCES message(id),
                conversation_id INTEGER NOT NULL REFERENCES conversation(id)
            );
            """
        )
        conn.executemany(
            "INSERT INTO participant(id, canonical_name, is_self) VALUES (?, ?, ?)",
            [
                (1, "Owner", 1),
                (2, "Owner", 1),
                (3, "Alice", 0),
                (4, " alice ", 0),
            ],
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
        conn.executemany(
            "INSERT INTO message(id, sender_id) VALUES (?, ?)",
            [(1001, 1), (1002, 2), (1003, 3)],
        )
        conn.executemany(
            "INSERT INTO message_conversation(id, message_id, conversation_id) VALUES (?, ?, 10)",
            [(101, 1001), (102, 1002), (103, 1003)],
        )
        conn.commit()

        participants = [
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
        messages = [
            CanonicalMessage(101, 1001, 10, 1, 0, "A"),
            CanonicalMessage(102, 1002, 10, 2, 1_000_000, "B"),
            CanonicalMessage(103, 1003, 10, 3, 2_000_000, "C"),
        ]
        result = process_messages(messages, participants=participants)
        store = ProcessingStore(conn, ROOT / "database" / "a3_schema.sql")
        store.initialize()
        run_id = store.persist(result, ProcessingConfig())
        conn.close()
        return path, run_id

    def test_independent_oracle_accepts_exact_conservative_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path, run_id = self._build_database(Path(tmp))
            report = validate_participant_resolution(path)
        self.assertEqual("PASS", report["status"], report)
        self.assertEqual(run_id, report["checks"]["processing_run_id"])
        self.assertEqual(4, report["checks"]["a2_participants"])
        self.assertEqual(3, report["checks"]["resolved_participants"])
        self.assertEqual(4, report["checks"]["participant_aliases"])
        self.assertEqual(1, report["checks"]["participant_candidates"])
        self.assertEqual(3, report["checks"]["resolved_sender_rows"])
        self.assertEqual(2, report["checks"]["resolved_sender_run_rows"])
        self.assertEqual(0, report["checks"]["foreign_key_errors"])

    def test_corrupt_alias_mapping_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path, run_id = self._build_database(Path(tmp))
            conn = sqlite3.connect(path)
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute(
                """UPDATE participant_alias
                   SET resolved_participant_id=3
                   WHERE processing_run_id=? AND participant_identity_id=11""",
                (run_id,),
            )
            conn.commit()
            conn.close()
            report = validate_participant_resolution(path)
        self.assertEqual("FAIL", report["status"])
        codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("A3_ALIAS_PROVENANCE_MISMATCH", codes)

    def test_missing_resolved_sender_mapping_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path, run_id = self._build_database(Path(tmp))
            conn = sqlite3.connect(path)
            conn.execute(
                """DELETE FROM processed_message_resolved_sender
                   WHERE processing_run_id=? AND membership_id=102""",
                (run_id,),
            )
            conn.commit()
            conn.close()
            report = validate_participant_resolution(path)
        self.assertEqual("FAIL", report["status"])
        codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("A3_RESOLVED_MESSAGE_SENDER_MISMATCH", codes)


if __name__ == "__main__":
    unittest.main()
