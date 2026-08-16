from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from a6.evidence import (
    FAIL,
    PASS,
    STALE,
    UNVERIFIED,
    PacketProvenanceError,
    enrich_analysis_packet_source_provenance,
    reconcile_a5_evidence_ref,
)


def packet():
    return {
        "schema_version": 1,
        "selected_message_ids": ["11"],
        "message_count": 1,
        "selected_message_count": 1,
        "messages": [{
            "membership_id": "101",
            "message_id": "11",
            "conversation_id": "7",
            "sender": "Alice",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "text": "ahoj",
            "selected": True,
        }],
    }


def build_db(path: Path, *, include_source: bool = True) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE import_run(id INTEGER PRIMARY KEY, parser_version TEXT);
            CREATE TABLE analysis_message_sources(
                message_id INTEGER,
                source_record_key TEXT,
                source_snapshot_key TEXT,
                import_run_id INTEGER
            );
            INSERT INTO import_run VALUES (1, 'parser-v4');
            """
        )
        if include_source:
            conn.execute(
                "INSERT INTO analysis_message_sources VALUES (11, 'rk-11', 'sha-11', 1)"
            )
        conn.commit()


def base_ref():
    return {
        "message_ids": ["11"],
        "messages": [{
            "message_id": "11",
            "membership_id": "101",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "sender_id": "Alice",
            "excerpt": "ahoj",
            "source_record_keys": ["rk-11"],
            "source_snapshot_keys": ["sha-11"],
            "source_parser_versions": ["parser-v4"],
        }],
        "metrics": [],
    }


def current_rows():
    return [{"message_id": "11", "membership_id": "101", "conversation_id": "7"}]


def current_sources():
    return {"11": [{
        "source_record_key": "rk-11",
        "source_snapshot_key": "sha-11",
        "parser_version": "parser-v4",
    }]}


def test_production_packet_is_enriched_with_a2_source_chain(tmp_path):
    db = tmp_path / "messages.sqlite"
    build_db(db)
    result = enrich_analysis_packet_source_provenance(packet(), db)
    row = result["messages"][0]
    assert row["membership_id"] == "101"
    assert row["source_record_keys"] == ["rk-11"]
    assert row["source_snapshot_keys"] == ["sha-11"]
    assert row["source_parser_versions"] == ["parser-v4"]
    assert row["source_provenance_status"] == "complete"
    assert result["source_provenance_required"] is True
    assert result["source_provenance_status"] == "complete"


def test_production_packet_fails_when_source_chain_is_missing(tmp_path):
    db = tmp_path / "messages.sqlite"
    build_db(db, include_source=False)
    with pytest.raises(PacketProvenanceError, match="without source provenance"):
        enrich_analysis_packet_source_provenance(packet(), db)


def test_demo_packet_is_explicitly_unverified():
    result = enrich_analysis_packet_source_provenance(packet(), None)
    assert result["source_provenance_required"] is False
    assert result["source_provenance_status"] == "missing"
    assert result["source_provenance_missing_message_ids"] == ["11"]
    assert result["messages"][0]["source_record_keys"] == []


def test_unknown_provenance_contract_fails_closed(tmp_path):
    db = tmp_path / "messages.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE analysis_message_sources(message_id INTEGER)")
        conn.commit()
    with pytest.raises(PacketProvenanceError, match="lacks required columns"):
        enrich_analysis_packet_source_provenance(packet(), db)


def test_exact_materialized_evidence_match_passes():
    report = reconcile_a5_evidence_ref(base_ref(), current_rows(), current_sources())
    assert report.status == PASS
    assert report.mismatches == ()


def test_membership_change_fails():
    report = reconcile_a5_evidence_ref(
        base_ref(), [{"message_id": "11", "membership_id": "999"}], current_sources()
    )
    assert report.status == FAIL
    assert "A6_MEMBERSHIP_MISMATCH" in {item.code for item in report.mismatches}


def test_source_drift_is_stale_not_silently_replaced():
    changed = current_sources()
    changed["11"] = [{
        "source_record_key": "rk-new",
        "source_snapshot_key": "sha-new",
        "parser_version": "parser-v5",
    }]
    report = reconcile_a5_evidence_ref(base_ref(), current_rows(), changed)
    assert report.status == STALE
    codes = {item.code for item in report.mismatches}
    assert "A6_SOURCE_RECORD_KEYS_DRIFT" in codes
    assert "A6_SOURCE_SNAPSHOT_KEYS_DRIFT" in codes
    assert "A6_SOURCE_PARSER_VERSIONS_DRIFT" in codes


def test_legacy_evidence_without_materialized_snapshot_is_unverified():
    report = reconcile_a5_evidence_ref(
        {"message_ids": ["11"]}, current_rows(), current_sources()
    )
    assert report.status == UNVERIFIED


def test_materialized_production_snapshot_without_source_keys_fails():
    value = base_ref()
    value["messages"][0]["source_record_keys"] = []
    report = reconcile_a5_evidence_ref(value, current_rows(), current_sources())
    assert report.status == FAIL
    assert "A5_EVIDENCE_PROVENANCE_MISSING" in {item.code for item in report.mismatches}
