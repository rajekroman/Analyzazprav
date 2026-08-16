from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.analytics import AnalyticsConfig, AnalyticsStore, analyze_database, load_analytic_messages
from analyzazprav.processing import (
    ProcessingConfig,
    ProcessingStore,
    load_a2_projection,
    process_messages,
)


A2_MEMBERSHIP_FIXTURE = """
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


def create_membership_database() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(A2_MEMBERSHIP_FIXTURE)
    conn.executemany(
        "INSERT INTO participant(id, canonical_name, is_self) VALUES (?, ?, ?)",
        [(1, "Owner", 1), (2, "Owner", 1), (3, "Alice", 0)],
    )
    conn.executemany(
        """INSERT INTO participant_identity(
               id, participant_id, identity_type, normalized_value, original_value
           ) VALUES (?, ?, ?, ?, ?)""",
        [
            (11, 1, "phone", "+420111", "+420 111"),
            (12, 2, "email", "owner@example.cz", "Owner@example.cz"),
            (13, 3, "phone", "+420333", "+420 333"),
        ],
    )
    conn.executemany("INSERT INTO conversation(id) VALUES (?)", [(10,), (20,)])
    conn.executemany(
        """INSERT INTO message(
               id, conversation_id, sender_id, sent_at_utc_us,
               timezone_offset_min, message_type, text
           ) VALUES (?, ?, ?, ?, 60, 'text', ?)""",
        [
            (101, 10, 1, 0, "Ahoj"),
            (102, 10, 2, 1_000_000, "Ještě jedna"),
            (103, 10, 3, 2_000_000, "Odpověď"),
        ],
    )
    conn.executemany(
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
    conn.executemany(
        "INSERT INTO message_source(id, message_id, source_message_id, source_row_id) VALUES (?, ?, ?, ?)",
        [(1, 101, "m101", "1"), (2, 102, "m102", "2"), (3, 103, "m103", "3")],
    )
    conn.commit()
    return conn


class A4A3MembershipContractTests(unittest.TestCase):
    def test_a4_reads_exact_a3_memberships_and_resolved_senders(self) -> None:
        conn = create_membership_database()
        projection = load_a2_projection(conn)
        processed = process_messages(
            list(projection.messages),
            list(projection.relations),
            participants=list(projection.participants),
        )
        a3_store = ProcessingStore(conn, ROOT / "database" / "a3_schema.sql")
        a3_store.initialize()
        processing_run_id = a3_store.replace_all(processed, ProcessingConfig())
        self.assertGreater(processing_run_id, 0)

        messages = load_analytic_messages(conn)
        self.assertEqual(
            [
                (m.message_id, m.conversation_id, m.participant_id)
                for m in messages
            ],
            [
                (101, 10, 1),
                (102, 10, 1),
                (103, 10, 3),
                (101, 20, 1),
            ],
        )
        self.assertEqual(
            len({(m.conversation_id, m.message_id) for m in messages}),
            4,
        )

        results = {item.conversation_id: item for item in analyze_database(conn)}
        self.assertEqual(results[10].source_message_count, 3)
        self.assertEqual(results[20].source_message_count, 1)
        self.assertEqual(set(results[10].participant_metrics), {1, 3})
        self.assertEqual(results[10].participant_metrics[1]["message_count"], 2)

        a4_store = AnalyticsStore(conn)
        a4_store.initialize()
        run_id = a4_store.write_run(list(results.values()), AnalyticsConfig())
        self.assertGreater(run_id, 0)
        persisted = conn.execute(
            """SELECT conversation_id, participant_id, message_count
               FROM analysis_a4_participants
               ORDER BY conversation_id, participant_id"""
        ).fetchall()
        self.assertEqual(persisted, [(10, 1, 2), (10, 3, 1), (20, 1, 1)])
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
