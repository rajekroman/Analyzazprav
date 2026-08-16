from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from analiza_zprav_a1.importer import import_imessage
from analyzazprav.normalization import CanonicalDatabase, ingest_a1_staging_bundle
from analyzazprav.processing import (
    ProcessingConfig,
    ProcessingStore,
    load_a2_projection,
    process_messages,
)
from analyzazprav.qa import (
    STATUS_FAIL,
    STATUS_PASS,
    canonical_fingerprint,
    validate_staging_dir,
    validate_vertical_pipeline,
)


def _make_chat_db(path: Path) -> None:
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
    conn.execute("INSERT INTO chat VALUES(8, 'iMessage;+;group-a7')")
    conn.execute(
        "INSERT INTO message VALUES(10, 'A7-GUID-10', 'Ahoj', NULL, 1, ?, 0, 'iMessage', NULL)",
        (800_000_000 * 1_000_000_000,),
    )
    conn.execute(
        "INSERT INTO message VALUES(11, 'A7-GUID-11', 'Ano', NULL, NULL, ?, 1, 'iMessage', 'A7-GUID-10')",
        (800_000_005 * 1_000_000_000,),
    )
    conn.execute("INSERT INTO chat_message_join VALUES(7,10)")
    conn.execute("INSERT INTO chat_message_join VALUES(8,10)")
    conn.execute("INSERT INTO chat_message_join VALUES(7,11)")
    conn.execute("INSERT INTO chat_handle_join VALUES(7,1)")
    conn.execute("INSERT INTO chat_handle_join VALUES(8,1)")
    conn.execute("INSERT INTO chat_handle_join VALUES(8,2)")
    conn.execute(
        "INSERT INTO attachment VALUES(22, '~/Library/Messages/Attachments/a7.jpg', 'image/jpeg', 'a7.jpg', 1234)"
    )
    conn.execute("INSERT INTO message_attachment_join VALUES(10,22)")
    conn.commit()
    conn.close()


def _build_vertical(tmp_path: Path) -> tuple[Path, Path, str, str]:
    source = tmp_path / "chat.db"
    staging = tmp_path / "staging"
    database = tmp_path / "canonical.sqlite"
    _make_chat_db(source)

    a1 = import_imessage(source, staging)
    assert a1.messages_seen == 2
    assert a1.messages_emitted == 2
    assert a1.attachments_seen == 1
    assert a1.errors == 0

    staging_report = validate_staging_dir(staging)
    assert staging_report["status"] == STATUS_PASS, staging_report
    assert staging_report["counts"]["records"] == 2
    assert staging_report["counts"]["conversation_relations"] == 3

    db = CanonicalDatabase(database)
    try:
        db.initialize()
        a2 = ingest_a1_staging_bundle(db, staging)
        assert a2.messages == 2
        assert a2.attachments == 1
        assert a2.conversation_relations == 3
        before = canonical_fingerprint(db.conn)

        projection = load_a2_projection(db.conn)
        assert len(projection.messages) == 3  # message 10 has two memberships
        result = process_messages(list(projection.messages), list(projection.relations))
        store = ProcessingStore(db.conn)
        store.initialize()
        store.persist(result, ProcessingConfig())

        after = canonical_fingerprint(db.conn)
        assert before == after
    finally:
        db.close()
    return staging, database, before, after


def test_actual_a1_a2_a3_vertical_pipeline_passes_and_preserves_a2(tmp_path: Path) -> None:
    staging, database, before, after = _build_vertical(tmp_path)
    assert before == after

    report = validate_vertical_pipeline(staging, database)
    assert report["status"] == STATUS_PASS, report
    checks = report["checks"]
    assert checks["a1_record_count"] == 2
    assert checks["a1_attachment_count"] == 1
    assert checks["a1_conversation_relation_count"] == 3
    assert checks["a2_message_source_count"] == 2
    assert checks["a2_source_conversation_relation_count"] == 3
    assert checks["a2_total_membership_count"] == 3
    assert checks["a2_total_canonical_message_count"] == 2
    assert checks["a3_input_membership_count"] == 3
    assert checks["a3_output_membership_count"] == 3
    assert checks["a3_canonical_message_count"] == 2
    assert checks["a3_processed_membership_rows"] == 3
    assert checks["a3_memberships_without_source_record_provenance"] == 0
    assert checks["sqlite_integrity"] == "ok"
    assert checks["foreign_key_error_count"] == 0


def test_staging_validator_rejects_corrupted_imessage_source_record_key(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    staging = tmp_path / "staging"
    _make_chat_db(source)
    import_imessage(source, staging)

    records = [
        json.loads(line)
        for line in (staging / "messages.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    records[0]["source_record_key"] = "0" * 64
    (staging / "messages.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )

    report = validate_staging_dir(staging)
    assert report["status"] == STATUS_FAIL
    codes = {issue["code"] for issue in report["issues"]}
    assert "SOURCE_RECORD_KEY_MISMATCH" in codes


def test_vertical_validator_detects_dropped_a3_membership(tmp_path: Path) -> None:
    staging, database, _, _ = _build_vertical(tmp_path)

    conn = sqlite3.connect(database)
    try:
        latest_run = conn.execute(
            "SELECT MAX(id) FROM processing_run WHERE status='completed'"
        ).fetchone()[0]
        membership_id = conn.execute(
            """SELECT membership_id FROM processed_message
               WHERE processing_run_id=? ORDER BY membership_id LIMIT 1""",
            (latest_run,),
        ).fetchone()[0]
        conn.execute(
            "DELETE FROM processed_message WHERE processing_run_id=? AND membership_id=?",
            (latest_run, membership_id),
        )
        conn.commit()
    finally:
        conn.close()

    report = validate_vertical_pipeline(staging, database)
    assert report["status"] == STATUS_FAIL
    codes = {issue["code"] for issue in report["issues"]}
    assert "A3_OUTPUT_ACCOUNTING_MISMATCH" in codes
    assert "A2_A3_MEMBERSHIP_SET_MISMATCH" in codes
