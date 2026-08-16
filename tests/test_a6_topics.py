from __future__ import annotations

import sqlite3

import pytest

from a6.data import DataSourceError
from a6.topics import load_a4_topics


def _fixture(path, *, topic_ids='["1", "2"]', reconciliation_ok: int = 1, period_evidence_count: int = 2):
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE analysis_a4_reconciliation (conversation_id INTEGER, reconciliation_ok INTEGER)"
        )
        conn.execute("INSERT INTO analysis_a4_reconciliation VALUES (42, ?)", (reconciliation_ok,))
        conn.execute(
            "CREATE TABLE analysis_a4_topics (analytics_run_id INTEGER, conversation_id INTEGER, topic_key TEXT, method TEXT, normalized_phrase TEXT, ngram_size INTEGER, document_frequency INTEGER, document_frequency_ratio REAL, occurrence_count INTEGER, participant_count INTEGER, salience REAL, first_period_date TEXT, last_period_date TEXT, source_message_ids_json TEXT)"
        )
        conn.execute(
            "INSERT INTO analysis_a4_topics VALUES (1, 42, 'topic:test', 'lexical_ngram_v1', 'test phrase', 2, 2, 0.5, 3, 2, 1.7, '2026-08-01', '2026-08-02', ?) ",
            (topic_ids,),
        )
        conn.execute(
            "CREATE TABLE analysis_a4_topic_evidence (analytics_run_id INTEGER, conversation_id INTEGER, topic_key TEXT, message_id INTEGER, participant_id INTEGER, period_date TEXT, date_basis TEXT, occurrence_count INTEGER)"
        )
        conn.executemany(
            "INSERT INTO analysis_a4_topic_evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, 42, 'topic:test', 1, 7, '2026-08-01', 'utc', 2),
                (1, 42, 'topic:test', 2, 8, '2026-08-02', 'utc', 1),
            ],
        )
        conn.execute(
            "CREATE TABLE analysis_a4_topic_periods (analytics_run_id INTEGER, conversation_id INTEGER, topic_key TEXT, normalized_phrase TEXT, method TEXT, participant_id INTEGER, date_basis TEXT, period_kind TEXT, period_start TEXT, period_end TEXT, topic_message_count INTEGER, occurrence_count INTEGER, participant_period_message_count INTEGER, topic_message_share REAL)"
        )
        conn.execute(
            "INSERT INTO analysis_a4_topic_periods VALUES (1, 42, 'topic:test', 'test phrase', 'lexical_ngram_v1', 7, 'utc', 'week', '2026-07-27', '2026-08-02', 1, 2, 4, 0.25)"
        )
        conn.execute(
            "CREATE TABLE analysis_a4_topic_period_reconciliation (analytics_run_id INTEGER, conversation_id INTEGER, evidence_row_count INTEGER, topic_count INTEGER, evidence_message_count INTEGER, dated_evidence_row_count INTEGER, undated_evidence_row_count INTEGER, unknown_participant_evidence_row_count INTEGER)"
        )
        conn.execute(
            "INSERT INTO analysis_a4_topic_period_reconciliation VALUES (1, 42, ?, 1, 2, 2, 0, 0)",
            (period_evidence_count,),
        )
        conn.commit()


def test_load_a4_topics_preserves_exact_candidate_and_evidence_ids(tmp_path):
    db_path = tmp_path / "topics.sqlite"
    _fixture(db_path)
    loaded = load_a4_topics(db_path, "42")

    assert loaded.available
    topic = loaded.topics.iloc[0]
    assert topic.method == "lexical_ngram_v1"
    assert topic.normalized_phrase == "test phrase"
    assert topic.evidence_message_ids == ("1", "2")
    assert set(loaded.evidence.message_id) == {"1", "2"}
    assert loaded.periods.iloc[0].period_kind == "week"
    assert int(loaded.period_reconciliation.iloc[0].evidence_row_count) == 2


def test_topic_candidate_evidence_mismatch_fails_closed(tmp_path):
    db_path = tmp_path / "mismatch.sqlite"
    _fixture(db_path, topic_ids='["1"]')
    with pytest.raises(DataSourceError, match="topic evidence mismatch"):
        load_a4_topics(db_path, "42")


def test_topic_period_reconciliation_mismatch_fails_closed(tmp_path):
    db_path = tmp_path / "bad-period-recon.sqlite"
    _fixture(db_path, period_evidence_count=1)
    with pytest.raises(DataSourceError, match="period reconciliation nesedí"):
        load_a4_topics(db_path, "42")


def test_topics_fail_closed_when_a4_reconciliation_fails(tmp_path):
    db_path = tmp_path / "bad-a4-recon.sqlite"
    _fixture(db_path, reconciliation_ok=0)
    with pytest.raises(DataSourceError, match="reconciliation_ok=0"):
        load_a4_topics(db_path, "42")


def test_topics_are_empty_when_view_is_missing(tmp_path):
    db_path = tmp_path / "plain.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE messages (id INTEGER)")
        conn.commit()
    assert not load_a4_topics(db_path, "42").available
