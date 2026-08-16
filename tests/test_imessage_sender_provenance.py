import json
import sqlite3
from pathlib import Path

from analiza_zprav_a1.importer import import_imessage
from analiza_zprav_a1.relation_reconciliation import reconcile_bundle
from analyzazprav.normalization import CanonicalDatabase, ingest_a1_staging_bundle


def _make_handle_source(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY,
            guid TEXT,
            text TEXT,
            handle_id INTEGER,
            date INTEGER,
            is_from_me INTEGER,
            service TEXT
        );
        """
    )
    conn.execute("INSERT INTO handle VALUES(1, 'alice@example.com')")
    conn.execute("INSERT INTO handle VALUES(2, NULL)")
    base = 800_000_000 * 1_000_000_000
    conn.execute("INSERT INTO message VALUES(10, 'G10', 'resolved', 1, ?, 0, 'iMessage')", (base,))
    conn.execute("INSERT INTO message VALUES(11, 'G11', 'null-id', NULL, ?, 1, 'iMessage')", (base + 1,))
    conn.execute("INSERT INTO message VALUES(12, 'G12', 'missing-row', 99, ?, 0, 'iMessage')", (base + 2,))
    conn.execute("INSERT INTO message VALUES(13, 'G13', 'null-value', 2, ?, 0, 'iMessage')", (base + 3,))
    conn.commit()
    conn.close()


def _records(staging: Path) -> dict[str, dict]:
    return {
        row["source_message_id"]: row
        for row in (
            json.loads(line)
            for line in (staging / "messages.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }


def test_sender_handle_resolution_states_are_explicit_and_survive_a2(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    staging = tmp_path / "staging"
    canonical = tmp_path / "canonical.sqlite"
    _make_handle_source(source)

    stats = import_imessage(source, staging)
    assert stats.errors == 0
    assert stats.reconciliation_ok is True

    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["parser"]["version"] == "0.9.0"
    records = _records(staging)

    assert records["10"]["sender_handle"] == "alice@example.com"
    assert records["10"]["metadata"]["_a1_sender_relation"] == {
        "raw_handle_id": 1,
        "resolved_handle_rowid": 1,
        "handle": "alice@example.com",
        "resolution_status": "resolved",
    }
    assert records["11"]["sender_handle"] is None
    assert records["11"]["metadata"]["_a1_sender_relation"] == {
        "raw_handle_id": None,
        "resolution_status": "missing_handle_id",
    }
    assert records["12"]["sender_handle"] is None
    assert records["12"]["metadata"]["_a1_sender_relation"] == {
        "raw_handle_id": 99,
        "resolution_status": "missing_handle_row",
    }
    assert records["13"]["sender_handle"] is None
    assert records["13"]["metadata"]["_a1_sender_relation"] == {
        "raw_handle_id": 2,
        "resolved_handle_rowid": 2,
        "resolution_status": "handle_value_null",
    }

    report = json.loads((staging / "reconciliation.json").read_text(encoding="utf-8"))
    assert report["checks"]["source_sender_provenance_matches_snapshot"] is True
    assert report["raw_counts"]["source_sender_handle_ids_present"] == 3
    assert report["raw_counts"]["source_sender_handle_ids_resolved"] == 1
    assert report["raw_counts"]["source_sender_handle_ids_unresolved"] == 2
    assert report["raw_counts"]["source_sender_handle_ids_null"] == 1
    assert report["raw_counts"]["source_sender_handle_id_column_missing"] == 0

    db = CanonicalDatabase(canonical)
    try:
        db.initialize()
        result = ingest_a1_staging_bundle(db, staging)
        assert result.messages == 4
        rows = db.conn.execute(
            "SELECT source_message_id, metadata_json FROM message_source ORDER BY source_message_id"
        ).fetchall()
        stored = {str(row[0]): json.loads(row[1]) for row in rows}
        for message_id in records:
            assert stored[message_id]["_a1_sender_relation"] == records[message_id]["metadata"]["_a1_sender_relation"]
    finally:
        db.close()


def test_sender_handle_table_missing_is_preserved_without_guessing(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    staging = tmp_path / "staging"
    conn = sqlite3.connect(source)
    conn.execute(
        "CREATE TABLE message (ROWID INTEGER PRIMARY KEY, handle_id INTEGER, date INTEGER, is_from_me INTEGER)"
    )
    conn.execute("INSERT INTO message VALUES(1, 55, ?, 0)", (800_000_000 * 1_000_000_000,))
    conn.commit()
    conn.close()

    stats = import_imessage(source, staging)
    assert stats.errors == 0
    record = _records(staging)["1"]
    assert record["sender_handle"] is None
    assert record["metadata"]["_a1_sender_relation"] == {
        "raw_handle_id": 55,
        "resolution_status": "handle_table_missing",
    }


def test_sender_handle_id_column_missing_is_explicit(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    staging = tmp_path / "staging"
    conn = sqlite3.connect(source)
    conn.execute(
        "CREATE TABLE message (ROWID INTEGER PRIMARY KEY, date INTEGER, is_from_me INTEGER)"
    )
    conn.execute("INSERT INTO message VALUES(1, ?, 1)", (800_000_000 * 1_000_000_000,))
    conn.commit()
    conn.close()

    stats = import_imessage(source, staging)
    assert stats.errors == 0
    record = _records(staging)["1"]
    assert record["sender_handle"] is None
    assert record["metadata"]["_a1_sender_relation"] == {
        "raw_handle_id": None,
        "resolution_status": "handle_id_column_missing",
    }
    report = json.loads((staging / "reconciliation.json").read_text(encoding="utf-8"))
    assert report["raw_counts"]["source_sender_handle_id_column_missing"] == 1


def test_sender_provenance_or_public_sender_tampering_is_detected(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    staging = tmp_path / "staging"
    _make_handle_source(source)
    assert import_imessage(source, staging).reconciliation_ok is True

    records = [
        json.loads(line)
        for line in (staging / "messages.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    records[0]["metadata"]["_a1_sender_relation"]["raw_handle_id"] = 999
    records[1]["sender_handle"] = "invented@example.com"
    (staging / "messages.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in records),
        encoding="utf-8",
    )

    report = reconcile_bundle(staging, source)
    assert report["ok"] is False
    assert report["checks"]["source_sender_provenance_matches_snapshot"] is False
    assert "source_sender_provenance_matches_snapshot" in report["failed_checks"]
    assert report["sender_provenance"]["failure_count"] == 2
