import sqlite3
from pathlib import Path

from analiza_zprav_a1.importer import import_imessage


def test_chat_handle_join_without_rowid_fails_before_staging_write(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    staging = tmp_path / "staging"
    conn = sqlite3.connect(source)
    conn.execute(
        "CREATE TABLE message (ROWID INTEGER PRIMARY KEY, date INTEGER, is_from_me INTEGER)"
    )
    conn.execute("CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT)")
    conn.execute(
        """
        CREATE TABLE chat_handle_join (
            chat_id INTEGER,
            handle_id INTEGER,
            PRIMARY KEY(chat_id, handle_id)
        ) WITHOUT ROWID
        """
    )
    conn.commit()
    conn.close()

    try:
        import_imessage(source, staging)
    except ValueError as exc:
        assert "chat_handle_join must provide SQLite ROWID provenance" in str(exc)
    else:
        raise AssertionError("expected chat_handle_join WITHOUT ROWID to fail closed")
    assert not staging.exists()
