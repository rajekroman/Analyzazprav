import json
import sqlite3
from pathlib import Path

from analiza_zprav_a1.importer import import_imessage
from analiza_zprav_a1.attachment_reconciliation import reconcile_bundle


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
            handle_id INTEGER,
            date INTEGER,
            is_from_me INTEGER,
            service TEXT
        );
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
        """
    )
    conn.execute("INSERT INTO handle VALUES(1, 'alice@example.com')")
    conn.execute("INSERT INTO chat VALUES(7, 'iMessage;-;alice@example.com')")
    conn.execute("INSERT INTO chat VALUES(8, 'iMessage;+;group-8')")
    conn.execute("INSERT INTO chat VALUES(9, 'iMessage;+;unused-9')")
    conn.execute(
        "INSERT INTO message VALUES(10, 'GUID-10', 'Ahoj', 1, ?, 0, 'iMessage')",
        (800_000_000 * 1_000_000_000,),
    )

    # Source ROWID order deliberately differs from parser chat order.
    conn.execute("INSERT INTO chat_message_join VALUES(8,10)")  # ROWID 1
    conn.execute("INSERT INTO chat_message_join VALUES(7,10)")  # ROWID 2
    conn.execute("INSERT INTO chat_message_join VALUES(7,10)")  # ROWID 3 duplicate

    conn.execute("INSERT INTO chat_handle_join VALUES(7,1)")     # ROWID 1
    conn.execute("INSERT INTO chat_handle_join VALUES(7,1)")     # ROWID 2 valid duplicate occurrence
    conn.execute("INSERT INTO chat_handle_join VALUES(8,1)")     # ROWID 3
    conn.execute("INSERT INTO chat_handle_join VALUES(9,1)")     # ROWID 4 outside message domain
    conn.execute("INSERT INTO chat_handle_join VALUES(NULL,1)")  # ROWID 5 no chat target
    conn.commit()
    conn.close()


def _record(staging: Path) -> dict:
    return json.loads((staging / "messages.jsonl").read_text(encoding="utf-8"))


def test_exact_conversation_and_participant_join_rows_are_preserved_and_accounted(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    staging = tmp_path / "staging"
    _make_source(source)

    stats = import_imessage(source, staging)
    assert stats.errors == 0
    assert stats.reconciliation_ok is True
    assert stats.duplicates == 1
    assert stats.unsupported == 2

    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["parser"]["version"] == "0.11.0"
    assert manifest["counts"]["duplicates"] == 1
    assert manifest["counts"]["unsupported"] == 2

    record = _record(staging)
    relations = record["conversation_sources"]
    assert [item["raw_chat_rowid"] for item in relations] == [7, 8]

    chat7 = relations[0]["metadata"]["_a1_source_relation"]
    assert chat7["chat"] == {
        "raw_join_rowid": 2,
        "raw_chat_rowid": 7,
        "resolution_status": "resolved",
    }
    assert chat7["participant_relations"] == [
        {
            "source_relation_ordinal": 0,
            "raw_join_rowid": 1,
            "raw_chat_rowid": 7,
            "raw_handle_id": 1,
            "resolved_handle_rowid": 1,
            "handle": "alice@example.com",
            "resolution_status": "resolved",
        },
        {
            "source_relation_ordinal": 1,
            "raw_join_rowid": 2,
            "raw_chat_rowid": 7,
            "raw_handle_id": 1,
            "resolved_handle_rowid": 1,
            "handle": "alice@example.com",
            "resolution_status": "resolved",
        },
    ]

    chat8 = relations[1]["metadata"]["_a1_source_relation"]
    assert chat8["chat"] == {
        "raw_join_rowid": 1,
        "raw_chat_rowid": 8,
        "resolution_status": "resolved",
    }
    assert chat8["participant_relations"][0]["raw_join_rowid"] == 3

    report = json.loads((staging / "reconciliation.json").read_text(encoding="utf-8"))
    assert report["checks"]["source_relation_provenance_matches_snapshot"] is True
    assert report["checks"]["source_chat_handle_rows_accounted"] is True
    assert report["raw_counts"]["source_chat_handle_link_rows"] == 5
    assert report["raw_counts"]["source_relevant_chat_handle_link_rows"] == 3
    assert report["raw_counts"]["source_unreferenced_chat_handle_link_rows"] == 2
    assert report["raw_counts"]["source_unsupported_records"] == 2

    duplicate = report["duplicate_records"]
    assert any(
        item.get("record_type") == "chat_message_join"
        and item.get("source_identifier") == "3"
        and item.get("outcome") == "duplicate"
        for item in duplicate
    )
    unsupported = report["unsupported_records"]
    assert {
        (item.get("record_type"), item.get("source_identifier"), item.get("reason"))
        for item in unsupported
    } == {
        (
            "chat_handle_join",
            "4",
            "chat participant relation is outside imported message conversation domain",
        ),
        (
            "chat_handle_join",
            "5",
            "chat participant relation has no chat_id",
        ),
    }


def test_conversation_or_participant_join_row_tampering_is_detected(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    staging = tmp_path / "staging"
    _make_source(source)
    assert import_imessage(source, staging).reconciliation_ok is True

    record = _record(staging)
    relation = record["conversation_sources"][0]["metadata"]["_a1_source_relation"]
    relation["chat"]["raw_join_rowid"] = 999
    relation["participant_relations"][0]["raw_join_rowid"] = 998
    (staging / "messages.jsonl").write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    report = reconcile_bundle(staging, source)
    assert report["ok"] is False
    assert report["checks"]["source_relation_provenance_matches_snapshot"] is False
    assert "source_relation_provenance_matches_snapshot" in report["failed_checks"]
    assert report["relation_provenance"]["failure_count"] >= 1
