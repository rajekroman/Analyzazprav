import json
import sqlite3
from pathlib import Path

from analiza_zprav_a1.importer import import_imessage
from analiza_zprav_a1.relation_reconciliation import reconcile_bundle


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
    conn.execute("INSERT INTO handle VALUES(2, NULL)")
    conn.execute("INSERT INTO chat VALUES(7, 'iMessage;-;alice@example.com')")
    conn.execute(
        "INSERT INTO message VALUES(10, 'GUID-10', 'Ahoj', 1, ?, 0, 'iMessage')",
        (800_000_000 * 1_000_000_000,),
    )
    conn.execute("INSERT INTO chat_message_join VALUES(7,10)")
    conn.execute("INSERT INTO chat_message_join VALUES(8,10)")

    # Chat 7 contains all participant-resolution outcomes. Duplicate handle=1
    # is a real source occurrence and therefore remains represented twice.
    conn.execute("INSERT INTO chat_handle_join VALUES(7,NULL)")
    conn.execute("INSERT INTO chat_handle_join VALUES(7,1)")
    conn.execute("INSERT INTO chat_handle_join VALUES(7,1)")
    conn.execute("INSERT INTO chat_handle_join VALUES(7,2)")
    conn.execute("INSERT INTO chat_handle_join VALUES(7,99)")

    # Chat row 8 is deliberately absent, but its relation and participant row
    # remain valid source facts and must not disappear.
    conn.execute("INSERT INTO chat_handle_join VALUES(8,1)")
    conn.commit()
    conn.close()


def _record(staging: Path) -> dict:
    return json.loads((staging / "messages.jsonl").read_text(encoding="utf-8"))


def test_dangling_chat_and_handle_relations_are_preserved_and_reconciled(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    staging = tmp_path / "staging"
    _make_source(source)

    stats = import_imessage(source, staging)
    assert stats.errors == 0
    assert stats.reconciliation_ok is True

    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["parser"]["version"] == "0.10.0"

    record = _record(staging)
    by_chat = {
        relation["raw_chat_rowid"]: relation
        for relation in record["conversation_sources"]
    }
    assert set(by_chat) == {7, 8}

    resolved = by_chat[7]
    provenance = resolved["metadata"]["_a1_source_relation"]
    assert provenance["chat"] == {
        "raw_chat_rowid": 7,
        "resolution_status": "resolved",
    }
    assert resolved["participant_handles"] == [
        "alice@example.com",
        "alice@example.com",
    ]
    assert provenance["participant_relations"] == [
        {
            "source_relation_ordinal": 0,
            "raw_chat_rowid": 7,
            "raw_handle_id": None,
            "resolution_status": "missing_handle_id",
        },
        {
            "source_relation_ordinal": 1,
            "raw_chat_rowid": 7,
            "raw_handle_id": 1,
            "resolved_handle_rowid": 1,
            "handle": "alice@example.com",
            "resolution_status": "resolved",
        },
        {
            "source_relation_ordinal": 2,
            "raw_chat_rowid": 7,
            "raw_handle_id": 1,
            "resolved_handle_rowid": 1,
            "handle": "alice@example.com",
            "resolution_status": "resolved",
        },
        {
            "source_relation_ordinal": 3,
            "raw_chat_rowid": 7,
            "raw_handle_id": 2,
            "resolved_handle_rowid": 2,
            "resolution_status": "handle_value_null",
        },
        {
            "source_relation_ordinal": 4,
            "raw_chat_rowid": 7,
            "raw_handle_id": 99,
            "resolution_status": "missing_handle_row",
        },
    ]

    missing_chat = by_chat[8]
    assert missing_chat["source_conversation_key"] == "rowid:8"
    assert missing_chat["chat_guid"] is None
    assert missing_chat["metadata"]["_a1_source_relation"]["chat"] == {
        "raw_chat_rowid": 8,
        "resolution_status": "missing_chat_row",
    }
    assert missing_chat["participant_handles"] == ["alice@example.com"]

    report = json.loads((staging / "reconciliation.json").read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["checks"]["source_relation_provenance_matches_snapshot"] is True
    assert report["raw_counts"]["source_relation_provenance_relations"] == 2
    assert report["raw_counts"]["source_relevant_chat_handle_link_rows"] == 6
    assert report["raw_counts"]["source_unresolved_chat_references"] == 1
    assert report["raw_counts"]["source_unresolved_participant_relations"] == 3


def test_relation_provenance_tampering_is_detected(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    staging = tmp_path / "staging"
    _make_source(source)
    imported = import_imessage(source, staging)
    assert imported.reconciliation_ok is True

    record = _record(staging)
    record["conversation_sources"][0]["metadata"]["_a1_source_relation"][
        "participant_relations"
    ] = []
    (staging / "messages.jsonl").write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    report = reconcile_bundle(staging, source)
    assert report["ok"] is False
    assert report["checks"]["source_relation_provenance_matches_snapshot"] is False
    assert "source_relation_provenance_matches_snapshot" in report["failed_checks"]
    assert report["relation_provenance"]["failure_count"] >= 1
