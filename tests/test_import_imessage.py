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
    conn.execute("INSERT INTO chat VALUES(7, 'iMessage;-;+420123456789')")
    conn.execute(
        "INSERT INTO message VALUES(10, 'GUID-10', 'Ahoj', NULL, 1, ?, 0, 'iMessage', NULL)",
        (800_000_000 * 1_000_000_000,),
    )
    conn.execute("INSERT INTO chat_message_join VALUES(7,10)")
    conn.execute(
        "INSERT INTO attachment VALUES(22, '~/Library/Messages/Attachments/a.jpg', 'image/jpeg', 'a.jpg', 1234)"
    )
    conn.execute("INSERT INTO message_attachment_join VALUES(10,22)")
    conn.commit()
    conn.close()


def test_import_emits_a1_staging_contract(tmp_path: Path):
    source = tmp_path / "chat.db"
    output = tmp_path / "staging"
    make_chat_db(source)

    stats = import_imessage(source, output)

    assert stats.messages_seen == 1
    assert stats.attachments_seen == 1
    assert stats.errors == 0

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["contract_version"] == "1"
    assert manifest["source"]["sha256"] == stats.source_sha256
    assert manifest["counts"]["messages_seen"] == 1

    lines = (output / "messages.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["record_type"] == "message"
    assert record["source_type"] == "imessage_chat_db"
    assert record["source_message_id"] == "10"
    assert record["source_guid"] == "GUID-10"
    assert record["conversation_source_id"] == "7"
    assert record["sender_handle"] == "+420123456789"
    assert record["text"] == "Ahoj"
    assert record["raw_text"] == "Ahoj"
    assert record["text_source"] == "text"
    assert record["timestamp_precision"] == "nanosecond"
    assert len(record["attachments"]) == 1
    assert record["attachments"][0]["source_attachment_id"] == "22"
    assert record["source_record_key"]
    assert record["raw_payload"]["guid"] == "GUID-10"


def test_same_source_produces_same_record_key(tmp_path: Path):
    source = tmp_path / "chat.db"
    make_chat_db(source)
    first = tmp_path / "one"
    second = tmp_path / "two"
    import_imessage(source, first)
    import_imessage(source, second)
    one = json.loads((first / "messages.jsonl").read_text(encoding="utf-8"))
    two = json.loads((second / "messages.jsonl").read_text(encoding="utf-8"))
    assert one["source_record_key"] == two["source_record_key"]
