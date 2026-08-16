import json
import sqlite3
from pathlib import Path

import pytest

import analiza_zprav_a1.importer as importer_module
from analiza_zprav_a1.importer import import_imessage
from analiza_zprav_a1.reconciliation import reconcile_bundle
from analyzazprav.normalization import CanonicalDatabase, ingest_a1_staging_bundle


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
        "INSERT INTO message VALUES(10, 'GUID-10', 'Ahoj', NULL, 1, ?, 0, 'iMessage', NULL)",
        (800_000_000 * 1_000_000_000,),
    )
    conn.execute("INSERT INTO chat_message_join VALUES(7,10)")
    conn.execute("INSERT INTO chat_message_join VALUES(8,10)")
    # Deliberate duplicate relation: parser emits one semantic membership and
    # reconciliation must account for the extra raw row as a duplicate outcome.
    conn.execute("INSERT INTO chat_message_join VALUES(8,10)")
    conn.execute("INSERT INTO chat_handle_join VALUES(7,1)")
    conn.execute("INSERT INTO chat_handle_join VALUES(8,1)")
    conn.execute("INSERT INTO chat_handle_join VALUES(8,2)")
    conn.execute(
        "INSERT INTO attachment VALUES(22, NULL, 'image/jpeg', 'photo.jpg', 1234)"
    )
    conn.execute(
        "INSERT INTO attachment VALUES(23, NULL, 'application/pdf', 'orphan.pdf', 55)"
    )
    conn.execute("INSERT INTO message_attachment_join VALUES(10,22)")
    conn.commit()
    conn.close()


def test_reconciliation_accounts_source_rows_relations_and_outcomes(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    staging = tmp_path / "staging"
    canonical = tmp_path / "canonical.sqlite"
    _make_source(source)

    stats = import_imessage(source, staging)

    assert stats.messages_seen == 1
    assert stats.messages_emitted == 1
    assert stats.errors == 0
    assert stats.reconciliation_ok is True
    assert stats.unsupported == 1
    assert stats.duplicates == 1

    report = json.loads((staging / "reconciliation.json").read_text(encoding="utf-8"))
    assert report["status"] == "ok"
    assert report["raw_counts"]["source_message_rows"] == 1
    assert report["raw_counts"]["source_chat_message_link_rows"] == 3
    assert report["raw_counts"]["source_unique_conversation_relations"] == 2
    assert report["raw_counts"]["source_message_attachment_link_rows"] == 1
    assert report["raw_counts"]["source_attachment_rows"] == 2
    assert report["raw_counts"]["source_duplicate_records"] == 1
    assert report["raw_counts"]["source_unsupported_records"] == 1
    assert len(report["duplicate_records"]) == 1
    assert report["duplicate_records"][0]["outcome"] == "duplicate"
    assert report["unsupported_records"] == [
        {
            "outcome": "unsupported",
            "reason": "attachment row is not referenced by message_attachment_join",
            "record_type": "attachment",
            "source_identifier": "23",
        }
    ]
    assert all(report["checks"].values())

    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["unsupported"] == 1
    assert manifest["counts"]["duplicates"] == 1
    assert manifest["counts"]["errors"] == 0
    assert manifest["outputs"]["reconciliation"] == "reconciliation.json"

    db = CanonicalDatabase(canonical)
    try:
        db.initialize()
        normalized = ingest_a1_staging_bundle(db, staging)
        assert normalized.messages == 1
        assert normalized.attachments == 1
        assert normalized.conversation_relations == 2
        assert db.conn.execute("SELECT COUNT(*) FROM message_source").fetchone()[0] == 1
        assert db.conn.execute("SELECT COUNT(*) FROM message_conversation").fetchone()[0] == 2
        integrity = db.integrity_report()
        assert integrity["integrity"] == "ok"
        assert integrity["foreign_key_errors"] == []
    finally:
        db.close()


def test_reconciliation_detects_tampered_staging(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    staging = tmp_path / "staging"
    _make_source(source)
    imported = import_imessage(source, staging)
    assert imported.reconciliation_ok is True

    (staging / "messages.jsonl").write_text("", encoding="utf-8")
    report = reconcile_bundle(staging, source)

    assert report["ok"] is False
    assert "messages_emitted_matches_file" in report["failed_checks"]
    assert "source_message_rows_accounted" in report["failed_checks"]


def test_reconciliation_failure_marks_bundle_invalid_for_a2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "chat.db"
    staging = tmp_path / "staging"
    canonical = tmp_path / "canonical.sqlite"
    _make_source(source)

    def forced_failure(*args, **kwargs):
        return {
            "ok": False,
            "status": "failed",
            "failed_checks": ["forced_reconciliation_failure"],
            "unsupported_records": [],
            "duplicate_records": [],
        }

    monkeypatch.setattr(importer_module, "reconcile_bundle", forced_failure)
    stats = importer_module.import_imessage(source, staging)

    assert stats.errors == 1
    assert stats.reconciliation_ok is False

    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"]["message_errors"] == 0
    assert manifest["counts"]["reconciliation_errors"] == 1
    assert manifest["counts"]["errors"] == 1

    error_rows = [
        json.loads(line)
        for line in (staging / "errors.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert error_rows == [
        {
            "error": "A1 source reconciliation failed",
            "error_type": "ReconciliationError",
            "failed_checks": ["forced_reconciliation_failure"],
            "scope": "reconciliation",
        }
    ]

    db = CanonicalDatabase(canonical)
    try:
        db.initialize()
        with pytest.raises(
            ValueError,
            match="A1 staging manifest reports extraction errors",
        ):
            ingest_a1_staging_bundle(db, staging)
    finally:
        db.close()
