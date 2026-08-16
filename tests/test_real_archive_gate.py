from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from tools.real_archive_gate import (
    RealArchiveGateError,
    conversation_inventory,
    resolve_conversation,
    run_gate,
)


def _make_real_gate_chat_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE chat (
            ROWID INTEGER PRIMARY KEY,
            guid TEXT,
            display_name TEXT,
            chat_identifier TEXT,
            service_name TEXT
        );
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
    conn.execute("INSERT INTO handle VALUES(1, '+420777111222')")
    conn.execute(
        "INSERT INTO chat VALUES(7, 'iMessage;-;+420777111222', 'ILA', '+420777111222', 'iMessage')"
    )
    conn.execute("INSERT INTO chat_handle_join VALUES(7,1)")
    texts = [
        "Ahoj",
        "Ahoj také",
        "Jak se máš?",
        "Dobře, díky",
        "Uvidíme se zítra",
        "Ano",
        "Platí v šest",
        "Těším se",
    ]
    for index, text in enumerate(texts, start=1):
        rowid = 100 + index
        is_from_me = 1 if index % 2 == 0 else 0
        handle_id = None if is_from_me else 1
        apple_ns = (800_000_000 + index * 60) * 1_000_000_000
        conn.execute(
            "INSERT INTO message VALUES(?, ?, ?, NULL, ?, ?, ?, 'iMessage', NULL)",
            (rowid, f"GUID-{rowid}", text, handle_id, apple_ns, is_from_me),
        )
        conn.execute("INSERT INTO chat_message_join VALUES(7,?)", (rowid,))
    conn.commit()
    conn.close()


def test_resolver_never_fuzzy_selects_target():
    inventory = [
        {
            "conversation_id": 1,
            "title": "Ilona",
            "canonical_key": None,
            "participants": [],
            "sources": [],
        }
    ]
    with pytest.raises(RealArchiveGateError, match="no exact") as exc:
        resolve_conversation(inventory, target="ILA")
    assert exc.value.code == "TARGET_NOT_RESOLVED"


def test_resolver_fails_on_ambiguous_exact_target():
    inventory = [
        {
            "conversation_id": 1,
            "title": "ILA",
            "canonical_key": None,
            "participants": [],
            "sources": [],
        },
        {
            "conversation_id": 2,
            "title": None,
            "canonical_key": None,
            "participants": [{"participant_id": 9, "canonical_name": "ILA", "identities": []}],
            "sources": [],
        },
    ]
    with pytest.raises(RealArchiveGateError, match="multiple conversations") as exc:
        resolve_conversation(inventory, target="ILA")
    assert exc.value.code == "TARGET_AMBIGUOUS"


def test_explicit_conversation_id_is_authoritative():
    inventory = [
        {
            "conversation_id": 7,
            "title": None,
            "canonical_key": None,
            "participants": [],
            "sources": [],
        }
    ]
    result = resolve_conversation(inventory, conversation_id=7)
    assert result["conversation_id"] == 7
    assert result["selector"] == "conversation_id"


def test_real_chat_db_gate_runs_existing_a1_a7_pipeline_and_keeps_source_read_only(tmp_path: Path):
    source = tmp_path / "chat.db"
    workdir = tmp_path / "gate"
    _make_real_gate_chat_db(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()

    report = run_gate(chat_db=source, workdir=workdir, target="ILA")

    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert report["status"] == "PASS", report
    assert report["verdict"] == "VALID", report
    assert report["release_ready"] is True
    assert report["source"]["snapshot_method"] == "sqlite_online_backup_v1"
    assert report["resolved_conversation"]["selector"] == "exact_target"
    assert "source:0:display_name" in report["resolved_conversation"]["match_reasons"]
    assert report["a4_probe"]["status"] == "PASS"
    assert report["a5_probe"]["status"] == "PASS"
    assert report["a6_probe"]["status"] == "PASS"
    assert report["a6_probe"]["packet_source_provenance_status"] == "complete"
    assert report["a6_probe"]["a5_adapter_provenance_preserved"] is True
    assert (workdir / "real_archive_report.json").is_file()
    persisted = json.loads((workdir / "real_archive_report.json").read_text(encoding="utf-8"))
    assert persisted["release_ready"] is True
    assert all(step["status"] == "PASS" for step in persisted["steps"])

    inventory = conversation_inventory(Path(report["database"]))
    assert inventory[0]["sources"][0]["labels"]["display_name"] == "ILA"
    serialized = json.dumps(inventory, ensure_ascii=False)
    assert "Uvidíme se zítra" not in serialized


def test_nonempty_workdir_is_rejected_before_processing(tmp_path: Path):
    source = tmp_path / "chat.db"
    workdir = tmp_path / "gate"
    _make_real_gate_chat_db(source)
    workdir.mkdir()
    (workdir / "existing.txt").write_text("do not mix runs", encoding="utf-8")
    with pytest.raises(RealArchiveGateError) as exc:
        run_gate(chat_db=source, workdir=workdir, target="ILA")
    assert exc.value.code == "WORKDIR_NOT_EMPTY"
