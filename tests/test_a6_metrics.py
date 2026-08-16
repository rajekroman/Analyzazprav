from __future__ import annotations

import sqlite3

import pytest

from a6.data import DataSourceError
from a6.metrics import load_a4_conversation_metrics


def _fixture(path, *, reconciliation_ok: int = 1):
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE analysis_messages (id INTEGER, conversation_id INTEGER, sender_id INTEGER, sender_name TEXT)"
        )
        conn.executemany(
            "INSERT INTO analysis_messages VALUES (?, ?, ?, ?)",
            [(1, 42, 7, "Osoba A"), (2, 42, 8, "Osoba B"), (3, 99, 9, "Jiná osoba")],
        )
        conn.execute(
            "CREATE TABLE analysis_a4_daily (analytics_run_id INTEGER, conversation_id INTEGER, participant_id INTEGER, period_date TEXT, message_count INTEGER, word_count INTEGER, turn_count INTEGER, initiations INTEGER, question_count INTEGER, affection_marker_count INTEGER, negative_marker_count INTEGER, median_response_latency_seconds REAL, median_response_effort_ratio REAL, source_message_ids_json TEXT)"
        )
        conn.executemany(
            "INSERT INTO analysis_a4_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, 42, 7, "2026-08-01", 10, 50, 5, 2, 1, 0, 0, 90.0, 1.2, '["1"]'),
                (1, 42, 8, "2026-08-01", 8, 40, 4, 1, 0, 0, 0, 120.0, 0.9, '["2"]'),
                (1, 99, 9, "2026-08-01", 99, 99, 99, 99, 99, 99, 99, 99.0, 9.9, '["3"]'),
            ],
        )
        conn.execute(
            "CREATE TABLE analysis_a4_participants (analytics_run_id INTEGER, conversation_id INTEGER, participant_id INTEGER, message_count INTEGER, word_count INTEGER, character_count INTEGER, active_days INTEGER, turn_count INTEGER, initiations INTEGER, initiation_share REAL, question_count INTEGER, exclamation_count INTEGER, affection_marker_count INTEGER, negative_marker_count INTEGER, median_response_latency_seconds REAL, median_response_effort_ratio REAL, engagement_score REAL)"
        )
        conn.executemany(
            "INSERT INTO analysis_a4_participants VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, 42, 7, 10, 50, 200, 1, 5, 2, 0.67, 1, 0, 0, 0, 90.0, 1.2, 0.7),
                (1, 42, 8, 8, 40, 160, 1, 4, 1, 0.33, 0, 0, 0, 0, 120.0, 0.9, 0.6),
            ],
        )
        conn.execute(
            "CREATE TABLE analysis_a4_responses (id INTEGER, analytics_run_id INTEGER, conversation_id INTEGER, session_id INTEGER, from_participant_id INTEGER, responder_id INTEGER, previous_turn_id INTEGER, response_turn_id INTEGER, latency_seconds REAL, response_effort_ratio REAL)"
        )
        conn.executemany(
            "INSERT INTO analysis_a4_responses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(1, 1, 42, 1, 7, 8, 1, 2, 120.0, 0.9), (2, 1, 42, 1, 8, 7, 2, 3, 60.0, 1.2), (3, 1, 99, 2, 9, 9, 4, 5, 999.0, 9.9)],
        )
        conn.execute(
            "CREATE TABLE analysis_a4_conversations (analytics_run_id INTEGER, conversation_id INTEGER, source_message_count INTEGER, known_sender_message_count INTEGER, unknown_sender_message_count INTEGER, turn_count INTEGER, session_count INTEGER, message_reciprocity REAL, word_reciprocity REAL, turn_reciprocity REAL, initiation_reciprocity REAL)"
        )
        conn.executemany(
            "INSERT INTO analysis_a4_conversations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(1, 42, 18, 18, 0, 9, 1, 0.8, 0.75, 0.8, 0.5), (1, 99, 99, 99, 0, 99, 9, 1.0, 1.0, 1.0, 1.0)],
        )
        conn.execute(
            "CREATE TABLE analysis_a4_reconciliation (conversation_id INTEGER, reconciliation_ok INTEGER)"
        )
        conn.executemany(
            "INSERT INTO analysis_a4_reconciliation VALUES (?, ?)",
            [(42, reconciliation_ok), (99, 1)],
        )
        conn.commit()


def test_load_a4_metrics_filters_one_conversation_and_labels_participants(tmp_path):
    db_path = tmp_path / "a4.sqlite"
    _fixture(db_path)
    metrics = load_a4_conversation_metrics(db_path, "42")

    assert metrics.available
    assert set(metrics.daily["conversation_id"].astype(str)) == {"42"}
    assert set(metrics.participants["sender"]) == {"Osoba A", "Osoba B"}
    assert set(metrics.responses["responder"]) == {"Osoba A", "Osoba B"}
    assert set(metrics.responses["from_participant"]) == {"Osoba A", "Osoba B"}
    assert metrics.conversation.iloc[0].source_message_count == 18
    assert metrics.daily.iloc[0].period_date.strftime("%Y-%m-%d") == "2026-08-01"


def test_a4_metrics_fail_closed_when_published_reconciliation_fails(tmp_path):
    db_path = tmp_path / "a4-invalid.sqlite"
    _fixture(db_path, reconciliation_ok=0)
    with pytest.raises(DataSourceError, match="reconciliation_ok=0"):
        load_a4_conversation_metrics(db_path, "42")


def test_a4_metrics_fail_closed_when_reconciliation_row_is_missing(tmp_path):
    db_path = tmp_path / "a4-missing-recon.sqlite"
    _fixture(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM analysis_a4_reconciliation WHERE conversation_id = 42")
        conn.commit()
    with pytest.raises(DataSourceError, match="bez reconciliation řádku"):
        load_a4_conversation_metrics(db_path, "42")


def test_a4_metrics_are_empty_when_views_are_missing(tmp_path):
    db_path = tmp_path / "plain.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE messages (id INTEGER)")
        conn.commit()
    metrics = load_a4_conversation_metrics(db_path, "1")
    assert not metrics.available
    assert metrics.daily.empty
