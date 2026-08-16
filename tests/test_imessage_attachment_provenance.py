import json
import sqlite3
from pathlib import Path

from analiza_zprav_a1.attachment_reconciliation import (
    ATTACHMENT_RELATION_PAYLOAD_KEY,
    reconcile_bundle,
)
from analiza_zprav_a1.importer import import_imessage
from analyzazprav.normalization import CanonicalDatabase, ingest_a1_staging_bundle


def _make_source(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY,
            guid TEXT,
            text TEXT,
            date INTEGER,
            is_from_me INTEGER
        );
        CREATE TABLE attachment (
            ROWID INTEGER PRIMARY KEY,
            filename TEXT,
            mime_type TEXT,
            transfer_name TEXT,
            total_bytes INTEGER
        );
        CREATE TABLE message_attachment_join (
            message_id INTEGER,
            attachment_id INTEGER
        );
        """
    )
    conn.execute(
        "INSERT INTO message VALUES(10, 'GUID-10', 'attachments', ?, 1)",
        (800_000_000 * 1_000_000_000,),
    )
    conn.execute("INSERT INTO attachment VALUES(20, 'a.jpg', 'image/jpeg', 'a.jpg', 10)")
    conn.execute("INSERT INTO attachment VALUES(21, 'b.pdf', 'application/pdf', 'b.pdf', 20)")

    # Deliberately insert target 21 before 20. A1 retains the historical primary
    # ordering by attachment ROWID, then uses join ROWID only to stabilize ties.
    conn.execute("INSERT INTO message_attachment_join VALUES(10,21)")  # join ROWID 1
    conn.execute("INSERT INTO message_attachment_join VALUES(10,20)")  # join ROWID 2
    conn.execute("INSERT INTO message_attachment_join VALUES(10,20)")  # join ROWID 3
    conn.execute("INSERT INTO message_attachment_join VALUES(10,99)")  # dangling / unsupported
    conn.commit()
    conn.close()


def _record(staging: Path) -> dict:
    return json.loads((staging / "messages.jsonl").read_text(encoding="utf-8"))


def test_valid_attachment_join_occurrences_are_exact_and_survive_a2(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    staging = tmp_path / "staging"
    canonical = tmp_path / "canonical.sqlite"
    _make_source(source)

    stats = import_imessage(source, staging)
    assert stats.errors == 0
    assert stats.reconciliation_ok is True
    assert stats.attachments_seen == 3
    assert stats.unsupported == 1

    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["parser"]["version"] == "0.10.0"

    record = _record(staging)
    assert [item["source_attachment_id"] for item in record["attachments"]] == [
        "20",
        "20",
        "21",
    ]
    provenance = [
        item["raw_payload"][ATTACHMENT_RELATION_PAYLOAD_KEY]
        for item in record["attachments"]
    ]
    assert provenance == [
        {
            "source_relation_ordinal": 0,
            "raw_join_rowid": 2,
            "raw_message_id": 10,
            "raw_attachment_id": 20,
            "resolution_status": "resolved",
        },
        {
            "source_relation_ordinal": 1,
            "raw_join_rowid": 3,
            "raw_message_id": 10,
            "raw_attachment_id": 20,
            "resolution_status": "resolved",
        },
        {
            "source_relation_ordinal": 2,
            "raw_join_rowid": 1,
            "raw_message_id": 10,
            "raw_attachment_id": 21,
            "resolution_status": "resolved",
        },
    ]
    # Original attachment source columns remain present next to the reserved
    # A1 provenance namespace rather than being replaced by it.
    assert record["attachments"][0]["raw_payload"]["filename"] == "a.jpg"

    report = json.loads((staging / "reconciliation.json").read_text(encoding="utf-8"))
    assert report["checks"]["source_attachment_relation_provenance_matches_snapshot"] is True
    assert report["raw_counts"]["source_valid_attachment_relation_rows"] == 3
    assert report["raw_counts"]["source_attachment_relation_provenance_occurrences"] == 3
    assert any(
        item.get("record_type") == "message_attachment_join"
        and item.get("attachment_id") == "99"
        and item.get("outcome") == "unsupported"
        for item in report["unsupported_records"]
    )

    db = CanonicalDatabase(canonical)
    try:
        db.initialize()
        result = ingest_a1_staging_bundle(db, staging)
        assert result.messages == 1
        assert result.attachments == 3
        rows = db.conn.execute(
            "SELECT source_attachment_id, raw_payload_json FROM attachment_source ORDER BY id"
        ).fetchall()
        assert len(rows) == 3
        stored = [json.loads(row[1]) for row in rows]
        assert [payload[ATTACHMENT_RELATION_PAYLOAD_KEY]["raw_join_rowid"] for payload in stored] == [
            2,
            3,
            1,
        ]
    finally:
        db.close()


def test_attachment_relation_provenance_tampering_is_detected(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    staging = tmp_path / "staging"
    _make_source(source)
    assert import_imessage(source, staging).reconciliation_ok is True

    record = _record(staging)
    record["attachments"][0]["raw_payload"][ATTACHMENT_RELATION_PAYLOAD_KEY][
        "raw_join_rowid"
    ] = 999
    (staging / "messages.jsonl").write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    report = reconcile_bundle(staging, source)
    assert report["ok"] is False
    assert report["checks"]["source_attachment_relation_provenance_matches_snapshot"] is False
    assert "source_attachment_relation_provenance_matches_snapshot" in report["failed_checks"]
    assert report["attachment_relation_provenance"]["failure_count"] == 1
