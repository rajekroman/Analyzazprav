import json
import sqlite3
from pathlib import Path

from analiza_zprav_a1.importer import import_imessage
from analiza_zprav_a1.reconciliation import reconcile_bundle
from analyzazprav.normalization import CanonicalDatabase, ingest_a1_staging_bundle

ROOT = Path(__file__).resolve().parents[1]


def make_source(path: Path) -> None:
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
    conn.execute("INSERT INTO attachment VALUES(22, NULL, 'image/jpeg', 'photo.jpg', 1234)")
    conn.execute("INSERT INTO attachment VALUES(23, NULL, 'application/pdf', 'orphan.pdf', 55)")
    conn.execute("INSERT INTO message_attachment_join VALUES(10,22)")
    conn.commit()
    conn.close()


def test_imessage_reconciliation_accounts_raw_rows_and_handoff_to_a2(tmp_path: Path):
    source = tmp_path / "chat.db"
    staging = tmp_path / "staging"
    make_source(source)

    stats = import_imessage(source, staging)

    assert stats.errors == 0
    assert stats.reconciliation_ok is True
    assert stats.unsupported == 1

    report = json.loads((staging / "reconciliation.json").read_text(encoding="utf-8"))
    assert report["status"] == "ok"
    assert report["raw_counts"]["source_message_rows"] == 1
    assert report["raw_counts"]["source_chat_message_links"] == 1
    assert report["raw_counts"]["source_message_attachment_links"] == 1
    assert report["raw_counts"]["source_unreferenced_attachments"] == 1
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
    assert manifest["counts"]["errors"] == 0
    assert manifest["outputs"]["reconciliation"] == "reconciliation.json"

    db = CanonicalDatabase(
        tmp_path / "canonical.sqlite",
        migrations_path=ROOT / "database" / "migrations",
    )
    try:
        db.initialize()
        result = ingest_a1_staging_bundle(db, staging)
        assert result.messages == 1
        assert result.attachments == 1
        assert db.conn.execute("SELECT COUNT(*) FROM message_source").fetchone()[0] == 1
    finally:
        db.close()


def test_reconciliation_detects_tampered_staging(tmp_path: Path):
    source = tmp_path / "chat.db"
    staging = tmp_path / "staging"
    make_source(source)
    import_imessage(source, staging)

    (staging / "messages.jsonl").write_text("", encoding="utf-8")
    report = reconcile_bundle(staging, source)

    assert report["ok"] is False
    assert "messages_emitted_matches_file" in report["failed_checks"]
    assert "source_message_rows_accounted" in report["failed_checks"]
