import json
import sqlite3
from pathlib import Path

from analiza_zprav_a1.importer import import_imessage
from analyzazprav.normalization import CanonicalDatabase, ingest_a1_staging_bundle


def _make_multichat_chat_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT);
        CREATE TABLE message (
          ROWID INTEGER PRIMARY KEY,
          guid TEXT,
          text TEXT,
          attributedBody BLOB,
          handle_id INTEGER,
          date INTEGER,
          is_from_me INTEGER,
          service TEXT,
          thread_originator_guid TEXT
        );
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
        CREATE TABLE attachment (
          ROWID INTEGER PRIMARY KEY,
          filename TEXT,
          mime_type TEXT,
          transfer_name TEXT,
          total_bytes INTEGER
        );
        CREATE TABLE message_attachment_join (message_id INTEGER, attachment_id INTEGER);
        """
    )
    conn.execute("INSERT INTO handle VALUES(1, '+420111222333')")
    conn.execute("INSERT INTO handle VALUES(2, '+420999888777')")
    conn.execute("INSERT INTO chat VALUES(7, 'iMessage;-;+420111222333')")
    conn.execute("INSERT INTO chat VALUES(8, 'iMessage;+;group-abc')")
    conn.execute(
        "INSERT INTO message VALUES(10, 'GUID-E2E-10', 'Ahoj', NULL, 1, ?, 0, 'iMessage', NULL)",
        (800_000_000 * 1_000_000_000,),
    )
    conn.execute("INSERT INTO chat_message_join VALUES(7,10)")
    conn.execute("INSERT INTO chat_message_join VALUES(8,10)")
    conn.execute("INSERT INTO chat_handle_join VALUES(7,1)")
    conn.execute("INSERT INTO chat_handle_join VALUES(8,1)")
    conn.execute("INSERT INTO chat_handle_join VALUES(8,2)")
    conn.execute(
        "INSERT INTO attachment VALUES(22, '~/Library/Messages/Attachments/e2e.jpg', 'image/jpeg', 'e2e.jpg', 1234)"
    )
    conn.execute("INSERT INTO message_attachment_join VALUES(10,22)")
    conn.commit()
    conn.close()


def test_imessage_multichat_roundtrip_into_a2_v5(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    staging = tmp_path / "staging"
    canonical_path = tmp_path / "canonical.sqlite"
    _make_multichat_chat_db(source)

    a1 = import_imessage(source, staging)
    assert a1.messages_seen == 1
    assert a1.messages_emitted == 1
    assert a1.attachments_seen == 1
    assert a1.errors == 0

    staged = json.loads((staging / "messages.jsonl").read_text(encoding="utf-8"))
    source_record_key = staged["source_record_key"]
    assert len(staged["conversation_sources"]) == 2

    db = CanonicalDatabase(canonical_path)
    try:
        db.initialize()
        a2 = ingest_a1_staging_bundle(db, staging)
        assert a2.messages == 1
        assert a2.attachments == 1
        assert a2.conversation_relations == 2

        assert db.conn.execute("SELECT COUNT(*) FROM message").fetchone()[0] == 1
        assert db.conn.execute("SELECT COUNT(*) FROM message_source").fetchone()[0] == 1
        assert db.conn.execute("SELECT COUNT(*) FROM conversation").fetchone()[0] == 2
        assert db.conn.execute("SELECT COUNT(*) FROM conversation_source").fetchone()[0] == 2
        assert db.conn.execute("SELECT COUNT(*) FROM message_conversation").fetchone()[0] == 2
        assert db.conn.execute("SELECT COUNT(*) FROM message_source_conversation").fetchone()[0] == 2
        assert db.conn.execute("SELECT COUNT(*) FROM message_attachment_occurrence").fetchone()[0] == 1

        stored_key = db.conn.execute(
            "SELECT source_record_key FROM message_source"
        ).fetchone()[0]
        assert stored_key == source_record_key

        source_conversations = db.conn.execute(
            """SELECT source_conversation_id, source_snapshot_key
               FROM conversation_source ORDER BY source_conversation_id"""
        ).fetchall()
        assert [row[0] for row in source_conversations] == [
            "guid:iMessage;+;group-abc",
            "guid:iMessage;-;+420111222333",
        ]
        assert {row[1] for row in source_conversations} == {a1.source_sha256}

        analysis_rows = db.conn.execute(
            "SELECT membership_id, id, conversation_id FROM analysis_messages ORDER BY membership_id"
        ).fetchall()
        assert len(analysis_rows) == 2
        assert len({row[0] for row in analysis_rows}) == 2
        assert len({row[1] for row in analysis_rows}) == 1
        assert len({row[2] for row in analysis_rows}) == 2

        report = db.integrity_report()
        assert report["integrity"] == "ok"
        assert report["foreign_key_errors"] == []
    finally:
        db.close()
