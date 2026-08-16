from __future__ import annotations

from pathlib import Path
import sqlite3

from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _pipeline_fixture(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE analysis_messages (membership_id INTEGER, id INTEGER, conversation_id INTEGER, sender_id INTEGER, sender_name TEXT, sent_at_utc_us INTEGER, timestamp_precision TEXT, timestamp_quality TEXT, text TEXT)"
        )
        conn.executemany(
            "INSERT INTO analysis_messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (101, 1, 42, 7, "Osoba A", 1785571200000000, "microsecond", "exact", "První zpráva"),
                (102, 2, 42, 8, "Osoba B", 1785571320000000, "microsecond", "exact", "Odpověď"),
                (103, 3, 42, 7, "Osoba A", 1785571500000000, "microsecond", "exact", "Pokračování"),
                (104, 4, 42, 8, "Osoba B", None, "unknown", "unknown", "Zpráva bez času"),
            ],
        )
        conn.execute("CREATE TABLE analysis_conversations (id INTEGER, title TEXT, canonical_key TEXT)")
        conn.execute("INSERT INTO analysis_conversations VALUES (42, 'Pipeline kontakt', 'conversation-42')")
        conn.execute(
            "CREATE TABLE analysis_message_sources (message_id INTEGER, source_type TEXT, source_message_id TEXT, source_conversation_id TEXT, source_row_id TEXT, source_record_key TEXT, source_contract_version TEXT, raw_timestamp TEXT, raw_text TEXT, source_hash TEXT, import_run_id INTEGER)"
        )
        conn.executemany(
            "INSERT INTO analysis_message_sources VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "imessage", "guid-1", "chat-42", "1", "record-1", "1", "raw-1", "První zpráva", "hash-1", 1),
                (2, "imessage", "guid-2", "chat-42", "2", "record-2", "1", "raw-2", "Odpověď", "hash-2", 1),
                (4, "imessage", "guid-4", "chat-42", "4", "record-4", "1", None, "Zpráva bez času", "hash-4", 1),
            ],
        )
        conn.execute(
            "CREATE TABLE analysis_attachments (occurrence_id INTEGER, message_id INTEGER, attachment_id INTEGER, sha256 TEXT, mime_type TEXT, size_bytes INTEGER, filename TEXT, storage_path TEXT, availability TEXT, position INTEGER)"
        )
        conn.execute(
            "INSERT INTO analysis_attachments VALUES (900, 2, 9, 'sha-9', 'image/jpeg', 1234, 'photo.jpg', '/attachments/photo.jpg', 'available', 1)"
        )
        conn.execute(
            "CREATE TABLE analysis_attachment_sources (attachment_source_id INTEGER, attachment_id INTEGER, occurrence_id INTEGER, message_id INTEGER, position INTEGER, import_run_id INTEGER, source_type TEXT, source_snapshot_key TEXT, source_sha256 TEXT, parser_version TEXT, source_attachment_id TEXT, source_occurrence_key TEXT, original_filename TEXT, original_path TEXT)"
        )
        conn.execute(
            "INSERT INTO analysis_attachment_sources VALUES (901, 9, 900, 2, 1, 1, 'imessage', 'snapshot', 'source-sha', '0.6.0', 'att-guid', 'occ-key', 'photo.jpg', '~/Library/Messages/Attachments/photo.jpg')"
        )
        conn.execute(
            "CREATE TABLE analysis_a4_events (id INTEGER, conversation_id INTEGER, event_type TEXT, score REAL, start_at_utc_us INTEGER, end_at_utc_us INTEGER, factors_json TEXT, source_message_ids_json TEXT)"
        )
        conn.execute(
            "INSERT INTO analysis_a4_events VALUES (5, 42, 'conflict_candidate', 0.8, 1785571200000000, 1785571500000000, '{\"rapid_exchange\": true}', '[\"1\", \"2\"]')"
        )
        conn.execute(
            "CREATE TABLE analysis_a4_daily (conversation_id INTEGER, participant_id INTEGER, period_date TEXT, message_count INTEGER, initiations INTEGER, median_response_latency_seconds REAL)"
        )
        conn.executemany(
            "INSERT INTO analysis_a4_daily VALUES (?, ?, ?, ?, ?, ?)",
            [(42, 7, "2026-08-01", 2, 1, 180.0), (42, 8, "2026-08-01", 1, 0, 120.0)],
        )
        conn.execute(
            "CREATE TABLE analysis_a4_participants (conversation_id INTEGER, participant_id INTEGER, message_count INTEGER, active_days INTEGER, initiations INTEGER, initiation_share REAL, median_response_latency_seconds REAL, median_response_effort_ratio REAL, engagement_score REAL)"
        )
        conn.executemany(
            "INSERT INTO analysis_a4_participants VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(42, 7, 2, 1, 1, 1.0, 180.0, 1.1, 0.7), (42, 8, 1, 1, 0, 0.0, 120.0, 0.9, 0.5)],
        )
        conn.execute(
            "CREATE TABLE analysis_a4_responses (conversation_id INTEGER, from_participant_id INTEGER, responder_id INTEGER, latency_seconds REAL, response_effort_ratio REAL)"
        )
        conn.executemany(
            "INSERT INTO analysis_a4_responses VALUES (?, ?, ?, ?, ?)",
            [(42, 7, 8, 120.0, 0.9), (42, 8, 7, 180.0, 1.1)],
        )
        conn.execute(
            "CREATE TABLE analysis_a4_conversations (conversation_id INTEGER, source_message_count INTEGER, message_reciprocity REAL, initiation_reciprocity REAL)"
        )
        conn.execute("INSERT INTO analysis_a4_conversations VALUES (42, 4, 0.8, 0.5)")
        conn.commit()


def test_streamlit_app_renders_demo_workflow_without_exception():
    app = AppTest.from_file(APP_PATH, default_timeout=10)
    app.run()
    assert not app.exception
    assert app.title[0].value == "Analýza zpráv"
    assert len(app.tabs) == 6
    assert [tab.label for tab in app.tabs] == [
        "Konverzace", "Časová osa", "Grafy", "Významná období", "Vybrané zprávy", "Analýza",
    ]
    assert app.sidebar.radio[0].value == "Demo"
    assert app.sidebar.selectbox[0].label == "Kontakt"
    assert app.sidebar.date_input[0].label == "Období"


def test_streamlit_app_renders_a2_a4_sqlite_pipeline_without_exception(tmp_path):
    db_path = tmp_path / "pipeline.sqlite"
    _pipeline_fixture(db_path)
    app = AppTest.from_file(APP_PATH, default_timeout=10)
    app.run()
    app.sidebar.radio[0].set_value("SQLite").run()
    assert app.sidebar.text_input[0].label == "SQLite"
    app.sidebar.text_input[0].set_value(str(db_path)).run()

    assert not app.exception
    assert app.sidebar.radio[0].value == "SQLite"
    assert app.sidebar.selectbox[0].label == "Kontakt"
    assert app.sidebar.selectbox[0].value == "Pipeline kontakt"
    assert app.sidebar.checkbox[0].value is True
    metrics = {item.label: item.value for item in app.metric}
    assert metrics["Memberships"] == "4"
    assert metrics["Canonical zprávy"] == "4"
    assert metrics["Bez času"] == "1"
    assert metrics["A4 nálezy"] == "1"
