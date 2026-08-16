from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from a6.data import DataSourceError, demo_messages
from a6.findings import filter_findings, load_a4_findings, resolve_evidence
from a6.provenance import load_message_sources


def _a4_fixture(path, *, reconciliation_ok: int = 1):
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE analysis_a4_events (id INTEGER, conversation_id INTEGER, event_type TEXT, score REAL, start_at_utc_us INTEGER, end_at_utc_us INTEGER, factors_json TEXT, source_message_ids_json TEXT)"
        )
        conn.execute(
            "CREATE TABLE analysis_a4_changes (id INTEGER, conversation_id INTEGER, participant_id INTEGER, metric TEXT, period_date TEXT, value REAL, baseline_median REAL, robust_z_score REAL, direction TEXT, source_message_ids_json TEXT)"
        )
        conn.execute(
            "CREATE TABLE analysis_a4_regimes (conversation_id INTEGER, period_start TEXT, period_end TEXT, participant_a_id INTEGER, participant_a_direction TEXT, participant_a_score REAL, participant_b_id INTEGER, participant_b_direction TEXT, participant_b_score REAL, regime_type TEXT, source_message_ids_json TEXT)"
        )
        conn.execute(
            "CREATE TABLE analysis_a4_reconciliation (conversation_id INTEGER, reconciliation_ok INTEGER)"
        )
        conn.execute("INSERT INTO analysis_a4_reconciliation VALUES (?, ?)", (42, reconciliation_ok))
        start_us = int(pd.Timestamp("2026-08-02T10:00:00Z").timestamp() * 1_000_000)
        end_us = int(pd.Timestamp("2026-08-02T11:00:00Z").timestamp() * 1_000_000)
        conn.execute(
            "INSERT INTO analysis_a4_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (1, 42, "conflict_candidate", 0.8, start_us, end_us, '{"rapid_exchange": true}', '["101", "102"]'),
        )
        conn.execute(
            "INSERT INTO analysis_a4_changes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (2, 42, 7, "message_count", "2026-08-03", 12.0, 4.0, 3.2, "increasing", '["103"]'),
        )
        conn.execute(
            "INSERT INTO analysis_a4_regimes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (42, "2026-08-04", "2026-08-10", 7, "increase", 1.3, 8, "increase", 1.1, "mutual_approach", '["104", "105"]'),
        )
        conn.execute(
            "INSERT INTO analysis_a4_regimes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (42, "2026-08-11", "2026-08-17", 7, "stable", 0.1, 8, "stable", 0.1, "stable_or_mixed", '["106"]'),
        )
        conn.commit()


def test_loads_a4_significant_findings_and_preserves_evidence(tmp_path):
    db_path = tmp_path / "a4.sqlite"
    _a4_fixture(db_path)
    findings = load_a4_findings(db_path)
    assert set(findings["finding_type"]) == {"event", "change_point", "regime"}
    assert "stable_or_mixed" not in set(findings["label"])
    event = findings[findings.finding_id == "event:1"].iloc[0]
    assert event.evidence_message_ids == ("101", "102")
    assert event.start_timestamp == pd.Timestamp("2026-08-02T10:00:00Z")


def test_filter_findings_respects_conversation_and_period(tmp_path):
    db_path = tmp_path / "a4.sqlite"
    _a4_fixture(db_path)
    findings = load_a4_findings(db_path)
    selected = filter_findings(
        findings,
        conversation_ids=["42"],
        start=pd.Timestamp("2026-08-03T00:00:00Z"),
        end=pd.Timestamp("2026-08-03T23:59:59Z"),
    )
    assert list(selected.finding_id) == ["change:2"]
    assert filter_findings(findings, conversation_ids=["999"]).empty


def test_malformed_a4_evidence_fails_closed(tmp_path):
    db_path = tmp_path / "bad.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE analysis_a4_events (id INTEGER, conversation_id INTEGER, event_type TEXT, score REAL, start_at_utc_us INTEGER, end_at_utc_us INTEGER, factors_json TEXT, source_message_ids_json TEXT)"
        )
        conn.execute("INSERT INTO analysis_a4_events VALUES (1, 1, 'x', 1.0, 0, 0, '{}', 'not-json')")
        conn.commit()
    with pytest.raises(DataSourceError):
        load_a4_findings(db_path)


def test_duplicate_a4_evidence_ids_fail_closed(tmp_path):
    db_path = tmp_path / "duplicate.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE analysis_a4_events (id INTEGER, conversation_id INTEGER, event_type TEXT, score REAL, start_at_utc_us INTEGER, end_at_utc_us INTEGER, factors_json TEXT, source_message_ids_json TEXT)"
        )
        conn.execute("INSERT INTO analysis_a4_events VALUES (1, 1, 'x', 1.0, 0, 0, '{}', '[\"1\", \"1\"]')")
        conn.commit()
    with pytest.raises(DataSourceError, match="duplicitní ID"):
        load_a4_findings(db_path)


def test_a4_findings_fail_closed_when_reconciliation_fails(tmp_path):
    db_path = tmp_path / "unreconciled.sqlite"
    _a4_fixture(db_path, reconciliation_ok=0)
    with pytest.raises(DataSourceError, match="reconciliation_ok=0"):
        load_a4_findings(db_path)


def test_resolve_evidence_reports_missing_ids():
    frame = demo_messages()
    requested = [frame.iloc[0].message_id, "missing-id"]
    evidence, missing = resolve_evidence(frame, requested)
    assert list(evidence.message_id) == [frame.iloc[0].message_id]
    assert missing == ("missing-id",)


def test_load_message_sources_resolves_a2_provenance(tmp_path):
    db_path = tmp_path / "a2.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE analysis_message_sources (message_id INTEGER, source_type TEXT, source_message_id TEXT, source_conversation_id TEXT, source_row_id TEXT, source_record_key TEXT, source_contract_version TEXT, raw_timestamp TEXT, raw_text TEXT, source_hash TEXT, import_run_id INTEGER)"
        )
        conn.execute(
            "INSERT INTO analysis_message_sources VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (101, "imessage", "guid-1", "chat-1", "row-9", "stable-key", "1", "12345", "raw text", "hash", 3),
        )
        conn.commit()
    sources = load_message_sources(db_path, ["101"])
    assert len(sources) == 1
    assert sources.iloc[0].message_id == "101"
    assert sources.iloc[0].source_record_key == "stable-key"
    assert sources.iloc[0].source_message_id == "guid-1"


def test_provenance_is_empty_when_non_a2_source_has_no_view(tmp_path):
    db_path = tmp_path / "generic.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE messages (id INTEGER)")
        conn.commit()
    assert load_message_sources(db_path, ["1"]).empty
