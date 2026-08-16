from __future__ import annotations

import sqlite3

from a6.attachments import load_attachment_sources, load_message_attachments


def test_load_message_attachments_filters_and_orders_selected_messages(tmp_path):
    db_path = tmp_path / "a2.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE analysis_attachments (occurrence_id INTEGER, message_id INTEGER, attachment_id INTEGER, sha256 TEXT, mime_type TEXT, size_bytes INTEGER, filename TEXT, storage_path TEXT, availability TEXT, position INTEGER)"
        )
        conn.executemany(
            "INSERT INTO analysis_attachments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1002, 101, 11, "sha-b", "image/jpeg", 200, "b.jpg", "/data/b.jpg", "available", 2),
                (1001, 101, 10, "sha-a", "image/png", 100, "a.png", "/data/a.png", "available", 1),
                (1003, 102, 12, None, "application/pdf", None, "missing.pdf", None, "missing", None),
                (1999, 999, 99, "other", "text/plain", 1, "other.txt", "/data/other.txt", "available", 1),
            ],
        )
        conn.commit()

    attachments = load_message_attachments(db_path, ["102", "101"])
    assert list(attachments.message_id) == ["102", "101", "101"]
    assert list(attachments.attachment_id) == ["12", "10", "11"]
    assert list(attachments.occurrence_id) == ["1003", "1001", "1002"]
    assert attachments.iloc[0].availability == "missing"
    assert attachments.iloc[1].filename == "a.png"


def test_attachment_adapter_returns_empty_when_view_is_missing(tmp_path):
    db_path = tmp_path / "plain.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE messages (id INTEGER)")
        conn.commit()
    assert load_message_attachments(db_path, ["1"]).empty
    assert load_attachment_sources(db_path, ["1"]).empty


def test_load_attachment_sources_uses_stable_occurrence_view(tmp_path):
    db_path = tmp_path / "a2-v6.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE analysis_attachment_sources ("
            "attachment_source_id INTEGER, attachment_id INTEGER, occurrence_id INTEGER, message_id INTEGER, position INTEGER, "
            "import_run_id INTEGER, source_type TEXT, source_snapshot_key TEXT, source_sha256 TEXT, parser_version TEXT, "
            "source_attachment_id TEXT, source_occurrence_key TEXT, original_filename TEXT, original_path TEXT)"
        )
        conn.executemany(
            "INSERT INTO analysis_attachment_sources VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (2, 10, 1001, 101, 1, 9, 'imessage', 'snap', 'sha', '0.6', 'src-a', 'occ-a-2', 'a.png', '~/a.png'),
                (1, 10, 1001, 101, 1, 8, 'imessage', 'snap-old', 'sha-old', '0.5', 'src-a', 'occ-a-1', 'a.png', '~/a.png'),
                (3, 11, 1002, 101, 2, 9, 'imessage', 'snap', 'sha', '0.6', 'src-b', 'occ-b', 'b.jpg', '~/b.jpg'),
            ],
        )
        conn.commit()

    sources = load_attachment_sources(db_path, ["1002", "1001"])
    assert list(sources.occurrence_id) == ["1002", "1001", "1001"]
    assert list(sources.attachment_source_id) == ["3", "1", "2"]
    assert sources.iloc[1].source_occurrence_key == "occ-a-1"
    assert sources.iloc[2].parser_version == "0.6"
