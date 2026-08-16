import json
import sqlite3
from pathlib import Path

from analiza_zprav_a1.importer import import_imessage
from analyzazprav.normalization import CanonicalDatabase, ingest_a1_staging_bundle


def _make_apple_event_source(path: Path) -> None:
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
          thread_originator_guid TEXT,
          associated_message_guid TEXT,
          associated_message_type INTEGER,
          associated_message_emoji TEXT,
          associated_message_range_location INTEGER,
          associated_message_range_length INTEGER,
          date_edited INTEGER,
          date_retracted INTEGER,
          is_edited INTEGER,
          edit_history BLOB
        );
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
        """
    )
    conn.execute("INSERT INTO handle VALUES(1, '+420111222333')")
    conn.execute("INSERT INTO chat VALUES(7, 'iMessage;-;+420111222333')")

    sent = 800_000_000 * 1_000_000_000
    edited = 800_000_100 * 1_000_000_000
    conn.execute(
        """INSERT INTO message VALUES(
               10, 'GUID-10', 'Edited source text', NULL, 1, ?, 0, 'iMessage', NULL,
               NULL, NULL, NULL, NULL, NULL, ?, NULL, 1, ?
           )""",
        (sent, edited, sqlite3.Binary(b"\x01\x02\x03")),
    )
    conn.execute(
        """INSERT INTO message VALUES(
               11, 'GUID-11', NULL, NULL, 1, ?, 0, 'iMessage', NULL,
               'p:0/GUID-10', 2001, '👍', 0, 4, NULL, NULL, 0, NULL
           )""",
        (sent + 1_000_000_000,),
    )
    conn.execute("INSERT INTO chat_message_join VALUES(7,10)")
    conn.execute("INSERT INTO chat_message_join VALUES(7,11)")
    conn.execute("INSERT INTO chat_handle_join VALUES(7,1)")
    conn.commit()
    conn.close()


def _staged_records(staging: Path) -> dict[str, dict]:
    return {
        record["source_message_id"]: record
        for record in (
            json.loads(line)
            for line in (staging / "messages.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }


def test_apple_event_metadata_is_lossless_and_survives_a2(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    staging = tmp_path / "staging"
    canonical = tmp_path / "canonical.sqlite"
    _make_apple_event_source(source)

    stats = import_imessage(source, staging)
    assert stats.errors == 0
    assert stats.reconciliation_ok is True
    assert stats.messages_emitted == 2

    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["parser"]["version"] == "0.6.0"

    records = _staged_records(staging)
    edited = records["10"]
    event = records["11"]

    edit_state = edited["metadata"]["apple_edit_state"]
    assert edit_state["date_edited_raw"] == 800_000_100 * 1_000_000_000
    assert edit_state["date_edited_utc"].endswith("Z")
    assert edit_state["is_edited_raw"] == 1
    assert edit_state["edit_history_present"] is True
    assert edit_state["edit_history_bytes"] == 3
    assert edited["raw_payload"]["edit_history"] == {
        "encoding": "base64",
        "data": "AQID",
    }

    associated = event["metadata"]["apple_associated_message"]
    assert associated == {
        "associated_message_emoji": "👍",
        "associated_message_guid": "p:0/GUID-10",
        "associated_message_range_length": 4,
        "associated_message_range_location": 0,
        "associated_message_type": 2001,
    }
    # A1 preserves Apple's exact target string and numeric type. It deliberately
    # does not infer a semantic reaction name from undocumented numeric codes.
    assert event["raw_payload"]["associated_message_guid"] == "p:0/GUID-10"
    assert event["raw_payload"]["associated_message_type"] == 2001

    db = CanonicalDatabase(canonical)
    try:
        db.initialize()
        result = ingest_a1_staging_bundle(db, staging)
        assert result.messages == 2

        rows = db.conn.execute(
            "SELECT source_message_id, metadata_json FROM message_source ORDER BY source_message_id"
        ).fetchall()
        stored = {row[0]: json.loads(row[1]) for row in rows}
        assert stored["10"]["apple_edit_state"] == edit_state
        assert stored["11"]["apple_associated_message"] == associated

        integrity = db.integrity_report()
        assert integrity["integrity"] == "ok"
        assert integrity["foreign_key_errors"] == []
    finally:
        db.close()
