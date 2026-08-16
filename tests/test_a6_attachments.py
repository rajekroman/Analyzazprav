from __future__ import annotations

import sqlite3

from a6.attachments import load_message_attachments


def test_load_message_attachments_filters_and_orders_selected_messages(tmp_path):
    db_path = tmp_path / "a2.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE analysis_attachments (message_id INTEGER, attachment_id INTEGER, sha256 TEXT, mime_type TEXT, size_bytes INTEGER, filename TEXT, storage_path TEXT, availability TEXT, position INTEGER)"
        )
        conn.executemany(
            "INSERT INTO analysis_attachments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (101, 11, "sha-b", "image/jpeg", 200, "b.jpg", "/data/b.jpg", "available", 2),
                (101, 10, "sha-a", "image/png", 100, "a.png", "/data/a.png", "available", 1),
                (102, 12, None, "application/pdf", None, "missing.pdf", None, "missing", None),
                (999, 99, "other", "text/plain", 1, "other.txt", "/data/other.txt", "available", 1),
            ],
        )
        conn.commit()

    attachments = load_message_attachments(db_path, ["102", "101"])
    assert list(attachments.message_id) == ["102", "101", "101"]
    assert list(attachments.attachment_id) == [12, 10, 11]
    assert attachments.iloc[0].availability == "missing"
    assert attachments.iloc[1].filename == "a.png"


def test_attachment_adapter_returns_empty_when_view_is_missing(tmp_path):
    db_path = tmp_path / "plain.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE messages (id INTEGER)")
        conn.commit()
    assert load_message_attachments(db_path, ["1"]).empty
