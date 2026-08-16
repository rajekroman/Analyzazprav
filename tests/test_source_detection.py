import sqlite3
from pathlib import Path

from analiza_zprav_a1.source_detection import detect_source


def test_detects_apple_messages_sqlite_by_schema(tmp_path: Path):
    source = tmp_path / "messages.sqlite"
    conn = sqlite3.connect(source)
    conn.execute("CREATE TABLE message (date INTEGER, is_from_me INTEGER)")
    conn.commit()
    conn.close()

    result = detect_source(source)

    assert result.source_type == "imessage_chat_db"
    assert result.confidence == "exact"


def test_detects_imazing_csv_conservatively(tmp_path: Path):
    source = tmp_path / "export.csv"
    source.write_text(
        "Chat Session,Sender,Sent Date,Message\nRoman,Alice,2026-08-16T08:00:00+02:00,Ahoj\n",
        encoding="utf-8",
    )

    result = detect_source(source)

    assert result.source_type == "imazing_messages_csv"
    assert result.confidence == "high"


def test_detects_generic_csv_without_claiming_imazing(tmp_path: Path):
    source = tmp_path / "messages.csv"
    source.write_text("id,sender,text\n1,Alice,Ahoj\n", encoding="utf-8")

    result = detect_source(source)

    assert result.source_type == "generic_message_csv"


def test_txt_detection_requires_explicit_record_mode(tmp_path: Path):
    source = tmp_path / "messages.txt"
    source.write_text("Ahoj\nJak se mas?\n", encoding="utf-8")

    result = detect_source(source)

    assert result.source_type == "generic_message_text"
    assert result.requires_explicit_mode is True
