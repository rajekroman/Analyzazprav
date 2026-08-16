import json
import sqlite3
from pathlib import Path

from analiza_zprav_a1.importer import import_imessage


def _make_source(path: Path) -> None:
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
        """
    )
    conn.execute("INSERT INTO handle VALUES(1, '+420111222333')")
    conn.execute("INSERT INTO chat VALUES(7, 'iMessage;-;+420111222333')")
    base = 800_000_000 * 1_000_000_000
    conn.execute(
        "INSERT INTO message VALUES(10, 'GUID-10', 'Authoritative text', ?, 1, ?, 0, 'iMessage', NULL)",
        (sqlite3.Binary("Jiný fallback".encode("utf-8")), base),
    )
    conn.execute(
        "INSERT INTO message VALUES(11, 'GUID-11', NULL, ?, 1, ?, 0, 'iMessage', NULL)",
        (sqlite3.Binary(b"\x01" + "日本語 👍".encode("utf-8") + b"\x00"), base + 1_000_000_000),
    )
    conn.execute("INSERT INTO chat_message_join VALUES(7,10)")
    conn.execute("INSERT INTO chat_message_join VALUES(7,11)")
    conn.execute("INSERT INTO chat_handle_join VALUES(7,1)")
    conn.commit()
    conn.close()


def test_text_column_remains_authoritative_and_fallback_is_explicit(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    staging = tmp_path / "staging"
    _make_source(source)

    stats = import_imessage(source, staging)
    assert stats.errors == 0
    assert stats.reconciliation_ok is True

    records = {
        record["source_message_id"]: record
        for record in (
            json.loads(line)
            for line in (staging / "messages.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }

    direct = records["10"]
    fallback = records["11"]

    assert direct["text"] == "Authoritative text"
    assert direct["raw_text"] == "Authoritative text"
    assert direct["text_source"] == "text"

    assert fallback["text"] == "日本語 👍"
    assert fallback["raw_text"] is None
    assert fallback["text_source"] == "attributedBody"
    assert fallback["raw_payload"]["attributedBody"]["encoding"] == "base64"
