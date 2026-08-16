import sqlite3
from pathlib import Path

from analiza_zprav_a1.imessage_preflight import validate_imessage_snapshot
from analiza_zprav_a1.importer import import_imessage


def _expect_value_error(call, expected: str) -> None:
    try:
        call()
    except ValueError as exc:
        assert expected in str(exc)
    else:
        raise AssertionError(f"expected ValueError containing {expected!r}")


def _create_message_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY,
            date INTEGER,
            is_from_me INTEGER,
            text TEXT,
            handle_id INTEGER
        )
        """
    )


def test_preflight_accepts_minimal_supported_message_schema(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    conn = sqlite3.connect(source)
    _create_message_table(conn)
    conn.execute("INSERT INTO message VALUES(1, 800000000000000000, 1, 'hello', NULL)")
    conn.commit()
    conn.close()

    report = validate_imessage_snapshot(source)
    assert report == {
        "preflight_version": "1",
        "sqlite_quick_check": "ok",
        "message_required_columns": ["date", "is_from_me"],
        "present_parser_relation_tables": [],
    }


def test_import_rejects_malformed_chat_message_join_before_staging_write(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    output = tmp_path / "staging"
    conn = sqlite3.connect(source)
    _create_message_table(conn)
    conn.execute("CREATE TABLE chat_message_join (chat_id INTEGER)")
    conn.execute("INSERT INTO message VALUES(1, 800000000000000000, 1, 'hello', NULL)")
    conn.commit()
    conn.close()

    _expect_value_error(
        lambda: import_imessage(source, output),
        "chat_message_join missing parser columns: message_id",
    )
    assert not output.exists()


def test_import_rejects_malformed_attachment_join_before_staging_write(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    output = tmp_path / "staging"
    conn = sqlite3.connect(source)
    _create_message_table(conn)
    conn.execute("CREATE TABLE message_attachment_join (message_id INTEGER)")
    conn.commit()
    conn.close()

    _expect_value_error(
        lambda: import_imessage(source, output),
        "message_attachment_join missing parser columns: attachment_id",
    )
    assert not output.exists()


def test_import_rejects_chat_handle_join_without_handle_table(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    output = tmp_path / "staging"
    conn = sqlite3.connect(source)
    _create_message_table(conn)
    conn.execute("CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER)")
    conn.commit()
    conn.close()

    _expect_value_error(
        lambda: import_imessage(source, output),
        "chat_handle_join is present but required table 'handle' is missing",
    )
    assert not output.exists()


def test_import_rejects_handle_table_without_id_when_parser_would_use_it(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    output = tmp_path / "staging"
    conn = sqlite3.connect(source)
    _create_message_table(conn)
    conn.execute("CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()

    _expect_value_error(
        lambda: import_imessage(source, output),
        "handle missing parser columns: id",
    )
    assert not output.exists()


def test_import_allows_absent_optional_relation_tables_and_preserves_orphan(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    output = tmp_path / "staging"
    conn = sqlite3.connect(source)
    _create_message_table(conn)
    conn.execute("INSERT INTO message VALUES(1, 800000000000000000, 1, 'hello', NULL)")
    conn.commit()
    conn.close()

    stats = import_imessage(source, output)
    assert stats.errors == 0
    assert stats.reconciliation_ok is True
    record = __import__("json").loads((output / "messages.jsonl").read_text(encoding="utf-8"))
    assert record["conversation_source_id"] == "orphan:1"
    assert record["conversation_sources"][0]["metadata"] == {"orphan_source_message": True}
