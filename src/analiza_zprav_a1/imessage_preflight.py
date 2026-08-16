from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .attachment_reconciliation import ATTACHMENT_RELATION_PAYLOAD_KEY

PREFLIGHT_VERSION = "1"


def _readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    escaped = table.replace("'", "''")
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info('{escaped}')")}


def _missing(actual: set[str], required: set[str]) -> list[str]:
    return sorted(required - actual)


def _requires_rowid(conn: sqlite3.Connection, table: str, issues: list[str]) -> None:
    escaped = table.replace('"', '""')
    try:
        conn.execute(f'SELECT ROWID FROM "{escaped}" LIMIT 0')
    except sqlite3.OperationalError:
        issues.append(f"{table} must provide SQLite ROWID provenance")


def validate_imessage_snapshot(path: Path) -> dict[str, Any]:
    """Fail closed on source corruption or structural schemas the parser cannot read.

    This preflight validates only SQLite integrity and the minimal structural
    dependencies of the current A1 parser. It does not infer semantics for
    optional Apple columns and does not inspect message row values.
    """

    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)

    with _readonly(path) as conn:
        quick_rows = [str(row[0]) for row in conn.execute("PRAGMA quick_check")]
        if quick_rows != ["ok"]:
            detail = "; ".join(quick_rows) if quick_rows else "no result"
            raise ValueError(f"Apple Messages SQLite quick_check failed: {detail}")

        tables = _tables(conn)
        if "message" not in tables:
            raise ValueError("Unsupported Apple Messages schema: required table 'message' is missing")

        table_columns = {table: _columns(conn, table) for table in tables}
        issues: list[str] = []

        required_message = {"date", "is_from_me"}
        missing = _missing(table_columns["message"], required_message)
        if missing:
            issues.append(f"message missing required columns: {', '.join(missing)}")

        conditional_requirements: dict[str, set[str]] = {
            "chat_message_join": {"message_id", "chat_id"},
            "chat_handle_join": {"chat_id", "handle_id"},
            "message_attachment_join": {"message_id", "attachment_id"},
        }
        for table, required in conditional_requirements.items():
            if table not in tables:
                continue
            missing = _missing(table_columns[table], required)
            if missing:
                issues.append(f"{table} missing parser columns: {', '.join(missing)}")

        # Reconciliation already uses exact ROWID identity for these relation
        # tables. Reject WITHOUT ROWID variants before staging files exist.
        for table in ("chat_message_join", "message_attachment_join"):
            if table in tables:
                _requires_rowid(conn, table, issues)

        message_columns = table_columns["message"]
        handle_is_used = "handle_id" in message_columns or "chat_handle_join" in tables
        if handle_is_used and "handle" in tables:
            missing = _missing(table_columns["handle"], {"id"})
            if missing:
                issues.append(f"handle missing parser columns: {', '.join(missing)}")
        if "chat_handle_join" in tables and "handle" not in tables:
            issues.append("chat_handle_join is present but required table 'handle' is missing")

        if (
            "message_attachment_join" in tables
            and "attachment" in tables
            and ATTACHMENT_RELATION_PAYLOAD_KEY in table_columns["attachment"]
        ):
            issues.append(
                "attachment table uses reserved A1 provenance column name: "
                f"{ATTACHMENT_RELATION_PAYLOAD_KEY}"
            )

        if issues:
            raise ValueError("Unsupported Apple Messages schema: " + "; ".join(issues))

        return {
            "preflight_version": PREFLIGHT_VERSION,
            "sqlite_quick_check": "ok",
            "message_required_columns": sorted(required_message),
            "present_parser_relation_tables": sorted(
                table for table in conditional_requirements if table in tables
            ),
        }
