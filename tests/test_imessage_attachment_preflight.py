import sqlite3
from pathlib import Path

from analiza_zprav_a1.attachment_reconciliation import ATTACHMENT_RELATION_PAYLOAD_KEY
from analiza_zprav_a1.importer import import_imessage


def _expect_value_error(call, expected: str) -> None:
    try:
        call()
    except ValueError as exc:
        assert expected in str(exc)
    else:
        raise AssertionError(f"expected ValueError containing {expected!r}")


def _message_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE message (ROWID INTEGER PRIMARY KEY, date INTEGER, is_from_me INTEGER)"
    )


def test_attachment_join_without_rowid_fails_before_staging_write(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    staging = tmp_path / "staging"
    conn = sqlite3.connect(source)
    _message_table(conn)
    conn.execute("CREATE TABLE attachment (ROWID INTEGER PRIMARY KEY, filename TEXT)")
    conn.execute(
        """
        CREATE TABLE message_attachment_join (
            message_id INTEGER,
            attachment_id INTEGER,
            PRIMARY KEY(message_id, attachment_id)
        ) WITHOUT ROWID
        """
    )
    conn.commit()
    conn.close()

    _expect_value_error(
        lambda: import_imessage(source, staging),
        "message_attachment_join must provide SQLite ROWID provenance",
    )
    assert not staging.exists()


def test_reserved_attachment_provenance_column_fails_before_staging_write(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    staging = tmp_path / "staging"
    conn = sqlite3.connect(source)
    _message_table(conn)
    escaped = ATTACHMENT_RELATION_PAYLOAD_KEY.replace('"', '""')
    conn.execute(
        f'CREATE TABLE attachment (ROWID INTEGER PRIMARY KEY, "{escaped}" TEXT)'
    )
    conn.execute(
        "CREATE TABLE message_attachment_join (message_id INTEGER, attachment_id INTEGER)"
    )
    conn.commit()
    conn.close()

    _expect_value_error(
        lambda: import_imessage(source, staging),
        "attachment table uses reserved A1 provenance column name",
    )
    assert not staging.exists()
