from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tempfile
from typing import Iterator


@contextmanager
def consistent_sqlite_snapshot(source: Path) -> Iterator[Path]:
    """Create a temporary consistent SQLite snapshot without modifying source.

    SQLite's online backup API reads the logical database through a read-only
    connection, including committed WAL content visible to that connection. The
    parser and source SHA-256 can then operate on the same immutable temporary
    database rather than hashing `chat.db` while reading a potentially different
    live WAL state.
    """

    source = source.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)

    with tempfile.TemporaryDirectory(prefix="analyzazprav-a1-sqlite-") as tmp:
        snapshot = Path(tmp) / "snapshot.sqlite"
        uri = source.as_uri() + "?mode=ro"
        src = sqlite3.connect(uri, uri=True)
        dst = sqlite3.connect(snapshot)
        try:
            src.execute("PRAGMA query_only=ON")
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
            src.close()

        if not snapshot.is_file() or snapshot.stat().st_size == 0:
            raise RuntimeError("SQLite backup produced no readable snapshot")
        yield snapshot
