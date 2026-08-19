from __future__ import annotations

from pathlib import Path
import sqlite3

from analiza_zprav_a1.importer import import_imessage
from analyzazprav.qa import STATUS_PASS, validate_staging_bundle, validate_staging_dir
from analyzazprav.qa.reconciliation import _count_jsonl
from analyzazprav.qa.vertical import _load_bundle


def _make_chat_db(path: Path, text: str) -> None:
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
    conn.execute("INSERT INTO chat VALUES(7, 'iMessage;-;+420111222333')")
    conn.execute(
        "INSERT INTO message VALUES(10, 'A7-UNICODE-10', ?, NULL, 1, ?, 0, 'iMessage', NULL)",
        (text, 800_000_000 * 1_000_000_000),
    )
    conn.execute("INSERT INTO chat_message_join VALUES(7,10)")
    conn.execute("INSERT INTO chat_handle_join VALUES(7,1)")
    conn.commit()
    conn.close()


def test_a7_jsonl_readers_keep_unicode_line_separators_inside_one_record(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    staging = tmp_path / "staging"
    expected_text = "alpha\u2028beta\u2029gamma"
    _make_chat_db(source, expected_text)

    result = import_imessage(source, staging)
    assert result.messages_emitted == 1
    assert result.reconciliation_ok is True

    messages_path = staging / "messages.jsonl"
    raw = messages_path.read_text(encoding="utf-8")
    assert "\u2028" in raw
    assert "\u2029" in raw
    assert len(raw.splitlines()) == 3  # regression proof: Unicode splitlines() is unsafe here

    staging_report = validate_staging_dir(staging)
    assert staging_report["status"] == STATUS_PASS, staging_report
    assert staging_report["counts"]["records"] == 1

    bundle_report = validate_staging_bundle(staging)
    assert bundle_report["status"] == STATUS_PASS, bundle_report
    assert bundle_report["checks"]["reconciliation_message_rows_match_file"] is True

    row_count, failures = _count_jsonl(messages_path)
    assert row_count == 1
    assert failures == []

    _, records = _load_bundle(staging)
    assert len(records) == 1
    assert records[0]["text"] == expected_text
