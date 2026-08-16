import hashlib
import json
import sqlite3
from pathlib import Path

from analiza_zprav_a1.importer import import_imessage


def make_chat_db(path: Path):
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
    conn.execute("INSERT INTO handle VALUES(1, '+420123456789')")
    conn.execute("INSERT INTO handle VALUES(2, '+420987654321')")
    conn.execute("INSERT INTO chat VALUES(7, 'iMessage;-;+420123456789')")
    conn.execute(
        "INSERT INTO message VALUES(10, 'GUID-10', 'Ahoj', NULL, 1, ?, 0, 'iMessage', NULL)",
        (800_000_000 * 1_000_000_000,),
    )
    conn.execute("INSERT INTO chat_message_join VALUES(7,10)")
    conn.execute("INSERT INTO chat_handle_join VALUES(7,1)")
    conn.execute("INSERT INTO chat_handle_join VALUES(7,2)")
    conn.execute(
        "INSERT INTO attachment VALUES(22, '~/Library/Messages/Attachments/a.jpg', 'image/jpeg', 'a.jpg', 1234)"
    )
    conn.execute("INSERT INTO message_attachment_join VALUES(10,22)")
    conn.commit()
    conn.close()


def add_second_chat_for_same_message(path: Path):
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO chat VALUES(8, 'iMessage;+;group-123')")
    conn.execute("INSERT INTO chat_message_join VALUES(8,10)")
    conn.execute("INSERT INTO chat_handle_join VALUES(8,1)")
    conn.execute("INSERT INTO chat_handle_join VALUES(8,2)")
    conn.commit()
    conn.close()


def test_import_emits_a1_staging_contract(tmp_path: Path):
    source = tmp_path / "chat.db"
    output = tmp_path / "staging"
    make_chat_db(source)

    stats = import_imessage(source, output)

    assert stats.messages_seen == 1
    assert stats.messages_emitted == 1
    assert stats.attachments_seen == 1
    assert stats.attachments_resolved == 0
    assert stats.attachments_missing == 1
    assert stats.errors == 0

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["contract_version"] == "1"
    assert manifest["source"]["sha256"] == stats.source_sha256
    assert manifest["source"]["snapshot_method"] == "sqlite_online_backup_v1"
    assert manifest["source"]["snapshot_includes_committed_wal"] is True
    assert manifest["counts"]["messages_seen"] == 1
    assert manifest["counts"]["messages_emitted"] == 1
    assert manifest["parser"]["version"] == "0.5.0"
    assert manifest["source_record_key"]["version"] == "2"
    assert manifest["source_record_key"]["scope"] == "source_snapshot+message_rowid"

    lines = (output / "messages.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["record_type"] == "message"
    assert record["source_type"] == "imessage_chat_db"
    assert record["source_message_id"] == "10"
    assert record["source_guid"] == "GUID-10"
    assert record["conversation_source_id"] == "guid:iMessage;-;+420123456789"
    assert len(record["conversation_sources"]) == 1
    relation = record["conversation_sources"][0]
    assert relation["source_conversation_key"] == "guid:iMessage;-;+420123456789"
    assert relation["raw_chat_rowid"] == 7
    assert relation["chat_guid"] == "iMessage;-;+420123456789"
    assert relation["participant_handles"] == ["+420123456789", "+420987654321"]
    assert record["sender_handle"] == "+420123456789"
    assert record["conversation_participant_handles"] == ["+420123456789", "+420987654321"]
    assert record["conversation_metadata"]["guid"] == "iMessage;-;+420123456789"
    assert record["text"] == "Ahoj"
    assert record["raw_text"] == "Ahoj"
    assert record["text_source"] == "text"
    assert record["timestamp_precision"] == "nanosecond"
    assert len(record["attachments"]) == 1
    assert record["attachments"][0]["source_attachment_id"] == "22"
    assert record["attachments"][0]["resolution_status"] == "missing"
    assert record["source_record_key"]
    assert record["raw_payload"]["guid"] == "GUID-10"


def test_same_physical_message_with_two_chats_is_emitted_once(tmp_path: Path):
    source = tmp_path / "chat.db"
    output = tmp_path / "staging"
    make_chat_db(source)
    add_second_chat_for_same_message(source)

    stats = import_imessage(source, output)

    assert stats.messages_seen == 1
    assert stats.messages_emitted == 1
    assert stats.attachments_seen == 1
    records = [
        json.loads(line)
        for line in (output / "messages.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 1
    record = records[0]
    assert record["source_message_id"] == "10"
    assert [item["raw_chat_rowid"] for item in record["conversation_sources"]] == [7, 8]
    assert [item["source_conversation_key"] for item in record["conversation_sources"]] == [
        "guid:iMessage;-;+420123456789",
        "guid:iMessage;+;group-123",
    ]
    assert len(record["attachments"]) == 1


def test_same_source_produces_same_record_key(tmp_path: Path):
    source = tmp_path / "chat.db"
    make_chat_db(source)
    first = tmp_path / "one"
    second = tmp_path / "two"
    first_stats = import_imessage(source, first)
    second_stats = import_imessage(source, second)
    one = json.loads((first / "messages.jsonl").read_text(encoding="utf-8"))
    two = json.loads((second / "messages.jsonl").read_text(encoding="utf-8"))
    assert first_stats.source_sha256 == second_stats.source_sha256
    assert one["source_record_key"] == two["source_record_key"]


def test_committed_wal_content_is_included_in_same_hashed_snapshot(tmp_path: Path):
    source = tmp_path / "chat.db"
    output = tmp_path / "staging"
    make_chat_db(source)

    writer = sqlite3.connect(source)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute(
            "INSERT INTO message VALUES(11, 'GUID-11', 'Z WAL', NULL, 2, ?, 0, 'iMessage', NULL)",
            (800_000_001 * 1_000_000_000,),
        )
        writer.execute("INSERT INTO chat_message_join VALUES(7,11)")
        writer.commit()

        wal_path = Path(str(source) + "-wal")
        assert wal_path.is_file()
        assert wal_path.stat().st_size > 0

        main_before = hashlib.sha256(source.read_bytes()).hexdigest()
        wal_before = hashlib.sha256(wal_path.read_bytes()).hexdigest()

        stats = import_imessage(source, output)

        # A1 is a read-only consumer of the live source database. The logical
        # snapshot is materialized elsewhere and must not checkpoint/rewrite it.
        assert hashlib.sha256(source.read_bytes()).hexdigest() == main_before
        assert wal_path.is_file()
        assert hashlib.sha256(wal_path.read_bytes()).hexdigest() == wal_before

        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["source"]["sha256"] == stats.source_sha256
        assert manifest["source"]["snapshot_method"] == "sqlite_online_backup_v1"
        assert manifest["source"]["snapshot_includes_committed_wal"] is True

        records = [
            json.loads(line)
            for line in (output / "messages.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert [record["source_message_id"] for record in records] == ["10", "11"]
        assert [record["source_guid"] for record in records] == ["GUID-10", "GUID-11"]
        assert stats.messages_seen == 2
        assert stats.messages_emitted == 2
        assert all(record["source_sha256"] == stats.source_sha256 for record in records)
    finally:
        writer.close()
