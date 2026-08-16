import json
import sqlite3
from pathlib import Path

from analiza_zprav_a1.importer import import_imessage
from analiza_zprav_a1.reconciliation import reconcile_bundle
from analyzazprav.normalization import CanonicalDatabase, ingest_a1_staging_bundle


def _make_source(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version=7")
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
        CREATE INDEX message_date_idx ON message(date);
        """
    )
    conn.execute("INSERT INTO handle VALUES(1, '+420111222333')")
    conn.execute("INSERT INTO chat VALUES(7, 'iMessage;-;+420111222333')")
    conn.execute(
        "INSERT INTO message VALUES(10, 'GUID-10', 'PRIVATE-TEXT', NULL, 1, ?, 0, 'iMessage', NULL)",
        (800_000_000 * 1_000_000_000,),
    )
    conn.execute("INSERT INTO chat_message_join VALUES(7,10)")
    conn.execute("INSERT INTO chat_handle_join VALUES(7,1)")
    conn.commit()
    conn.close()


def test_import_emits_schema_report_and_a2_accepts_extended_bundle(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    staging = tmp_path / "staging"
    canonical = tmp_path / "canonical.sqlite"
    _make_source(source)

    stats = import_imessage(source, staging)
    assert stats.errors == 0
    assert stats.reconciliation_ok is True

    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    schema = json.loads((staging / "schema.json").read_text(encoding="utf-8"))
    reconciliation = json.loads(
        (staging / "reconciliation.json").read_text(encoding="utf-8")
    )

    assert manifest["parser"]["version"] == "0.7.0"
    assert manifest["outputs"]["schema"] == "schema.json"
    assert manifest["source"]["schema_inventory_version"] == "1"
    assert manifest["source"]["schema_signature_sha256"] == schema["signature_sha256"]
    assert schema["sqlite"]["user_version"] == 7
    assert "PRIVATE-TEXT" not in json.dumps(schema, ensure_ascii=False)
    assert reconciliation["schema"]["contract_declared"] is True
    assert reconciliation["schema"]["actual_signature_sha256"] == schema["signature_sha256"]
    assert reconciliation["schema"]["report_signature_sha256"] == schema["signature_sha256"]
    assert reconciliation["checks"]["imessage_schema_contract_complete"] is True
    assert reconciliation["checks"]["imessage_schema_report_parse"] is True
    assert reconciliation["checks"]["imessage_schema_signature_matches_snapshot"] is True
    assert reconciliation["checks"]["imessage_schema_report_matches_snapshot"] is True
    assert reconciliation["checks"]["imessage_schema_report_signature_matches_manifest"] is True

    db = CanonicalDatabase(canonical)
    try:
        db.initialize()
        result = ingest_a1_staging_bundle(db, staging)
        assert result.messages == 1
        integrity = db.integrity_report()
        assert integrity["integrity"] == "ok"
        assert integrity["foreign_key_errors"] == []
    finally:
        db.close()


def test_manual_reconciliation_detects_tampered_schema_report(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    staging = tmp_path / "staging"
    _make_source(source)
    imported = import_imessage(source, staging)
    assert imported.reconciliation_ok is True

    schema_path = staging / "schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["tables"][0]["columns"][0]["name"] = "tampered-column"
    schema_path.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    report = reconcile_bundle(staging, source)
    assert report["ok"] is False
    assert "imessage_schema_report_matches_snapshot" in report["failed_checks"]
    assert report["checks"]["imessage_schema_signature_matches_snapshot"] is True


def test_legacy_bundle_without_schema_contract_remains_reconcilable(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    staging = tmp_path / "staging"
    _make_source(source)
    imported = import_imessage(source, staging)
    assert imported.reconciliation_ok is True

    manifest_path = staging / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"].pop("schema_inventory_version")
    manifest["source"].pop("schema_signature_sha256")
    manifest["outputs"].pop("schema")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (staging / "schema.json").unlink()

    report = reconcile_bundle(staging, source)
    assert report["ok"] is True
    assert report["schema"]["contract_declared"] is False
    assert "imessage_schema_contract_complete" not in report["checks"]
