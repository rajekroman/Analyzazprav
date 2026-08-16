from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


STATUS_PASS = "PASS"
STATUS_WARNING = "WARNING"
STATUS_FAIL = "FAIL"

REQUIRED_TABLES = {
    "import_run",
    "participant",
    "participant_identity",
    "conversation",
    "conversation_source",
    "conversation_participant",
    "message",
    "message_source",
    "duplicate_candidate",
    "message_relation",
    "attachment",
    "message_attachment",
    "attachment_source",
}
REQUIRED_VIEWS = {"analysis_messages", "analysis_conversations", "analysis_attachments"}


def _issue(issues: list[dict[str, Any]], severity: str, code: str, detail: str) -> None:
    issues.append({"severity": severity, "code": code, "detail": detail})


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _scalar(conn: sqlite3.Connection, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0])


def validate_sqlite_database(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    issues: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    checks: dict[str, Any] = {}

    if not path.is_file():
        _issue(issues, "ERROR", "DATABASE_MISSING", f"SQLite database not found: {path}")
        return _finalize(path, issues, counts, checks)

    wal_path = Path(str(path) + "-wal")
    if wal_path.exists() and wal_path.stat().st_size > 0:
        _issue(
            issues,
            "WARNING",
            "SQLITE_WAL_PRESENT",
            "Database has a non-empty WAL; validate a stable/checkpointed snapshot for reproducible hashing.",
        )

    uri = f"{path.resolve().as_uri()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        _issue(issues, "ERROR", "DATABASE_OPEN_FAILED", str(exc))
        return _finalize(path, issues, counts, checks)

    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")

        try:
            integrity_rows = [row[0] for row in conn.execute("PRAGMA integrity_check")]
            checks["integrity_check"] = integrity_rows
            if integrity_rows != ["ok"]:
                _issue(
                    issues,
                    "ERROR",
                    "SQLITE_INTEGRITY_FAILED",
                    "; ".join(str(value) for value in integrity_rows[:20]),
                )
        except sqlite3.Error as exc:
            _issue(issues, "ERROR", "SQLITE_INTEGRITY_CHECK_FAILED", str(exc))

        try:
            fk_rows = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check")]
            checks["foreign_key_errors"] = len(fk_rows)
            if fk_rows:
                _issue(
                    issues,
                    "ERROR",
                    "SQLITE_FOREIGN_KEY_ERRORS",
                    f"{len(fk_rows)} foreign-key violation(s) detected",
                )
        except sqlite3.Error as exc:
            _issue(issues, "ERROR", "SQLITE_FOREIGN_KEY_CHECK_FAILED", str(exc))

        objects = {
            row["name"]: row["type"]
            for row in conn.execute(
                "SELECT name, type FROM sqlite_master WHERE type IN ('table','view')"
            )
        }
        missing_tables = sorted(REQUIRED_TABLES - {name for name, kind in objects.items() if kind == "table"})
        missing_views = sorted(REQUIRED_VIEWS - {name for name, kind in objects.items() if kind == "view"})
        if missing_tables:
            _issue(
                issues,
                "ERROR",
                "A2_REQUIRED_TABLES_MISSING",
                ", ".join(missing_tables),
            )
        if missing_views:
            _issue(
                issues,
                "ERROR",
                "A2_REQUIRED_VIEWS_MISSING",
                ", ".join(missing_views),
            )

        if missing_tables or missing_views:
            return _finalize(path, issues, counts, checks)

        for table in (
            "import_run",
            "conversation",
            "participant",
            "message",
            "message_source",
            "attachment",
            "attachment_source",
            "message_attachment",
        ):
            counts[table] = _scalar(conn, f"SELECT COUNT(*) FROM {table}")

        messages_without_source = _scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM message m
            WHERE NOT EXISTS (
                SELECT 1 FROM message_source ms WHERE ms.message_id = m.id
            )
            """,
        )
        checks["messages_without_source"] = messages_without_source
        if messages_without_source:
            _issue(
                issues,
                "ERROR",
                "MESSAGE_SOURCE_TRACE_MISSING",
                f"{messages_without_source} canonical message(s) have no message_source provenance",
            )

        attachments_without_source = _scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM attachment a
            WHERE NOT EXISTS (
                SELECT 1 FROM attachment_source s WHERE s.attachment_id = a.id
            )
            """,
        )
        checks["attachments_without_source"] = attachments_without_source
        if attachments_without_source:
            _issue(
                issues,
                "ERROR",
                "ATTACHMENT_SOURCE_TRACE_MISSING",
                f"{attachments_without_source} attachment(s) have no attachment_source provenance",
            )

        unattached_attachments = _scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM attachment a
            WHERE NOT EXISTS (
                SELECT 1 FROM message_attachment ma WHERE ma.attachment_id = a.id
            )
            """,
        )
        checks["unattached_attachments"] = unattached_attachments
        if unattached_attachments:
            _issue(
                issues,
                "WARNING",
                "ATTACHMENT_ORPHAN_CANONICAL",
                f"{unattached_attachments} canonical attachment(s) are not linked to a message",
            )

        empty_source_hashes = _scalar(
            conn,
            "SELECT COUNT(*) FROM message_source WHERE source_hash IS NULL OR trim(source_hash) = ''",
        )
        checks["empty_source_hashes"] = empty_source_hashes
        if empty_source_hashes:
            _issue(
                issues,
                "ERROR",
                "MESSAGE_SOURCE_HASH_MISSING",
                f"{empty_source_hashes} message_source row(s) have an empty source_hash",
            )

        running_imports = _scalar(conn, "SELECT COUNT(*) FROM import_run WHERE status='running'")
        checks["running_imports"] = running_imports
        if running_imports:
            _issue(
                issues,
                "WARNING",
                "IMPORT_RUN_IN_PROGRESS",
                f"{running_imports} import run(s) are still marked running",
            )

        invalid_completed_imports = _scalar(
            conn,
            "SELECT COUNT(*) FROM import_run WHERE status='completed' AND finished_at_utc_us IS NULL",
        )
        checks["completed_imports_without_finish"] = invalid_completed_imports
        if invalid_completed_imports:
            _issue(
                issues,
                "ERROR",
                "IMPORT_RUN_FINISH_TIMESTAMP_MISSING",
                f"{invalid_completed_imports} completed import run(s) have no finished_at_utc_us",
            )

        analysis_message_count = _scalar(conn, "SELECT COUNT(*) FROM analysis_messages")
        analysis_conversation_count = _scalar(conn, "SELECT COUNT(*) FROM analysis_conversations")
        analysis_attachment_count = _scalar(conn, "SELECT COUNT(*) FROM analysis_attachments")
        checks["analysis_messages_count"] = analysis_message_count
        checks["analysis_conversations_count"] = analysis_conversation_count
        checks["analysis_attachments_count"] = analysis_attachment_count

        if analysis_message_count != counts["message"]:
            _issue(
                issues,
                "ERROR",
                "ANALYSIS_MESSAGES_COUNT_MISMATCH",
                f"analysis_messages={analysis_message_count}, message={counts['message']}",
            )
        if analysis_conversation_count != counts["conversation"]:
            _issue(
                issues,
                "ERROR",
                "ANALYSIS_CONVERSATIONS_COUNT_MISMATCH",
                f"analysis_conversations={analysis_conversation_count}, conversation={counts['conversation']}",
            )
        if analysis_attachment_count != counts["message_attachment"]:
            _issue(
                issues,
                "ERROR",
                "ANALYSIS_ATTACHMENTS_COUNT_MISMATCH",
                f"analysis_attachments={analysis_attachment_count}, message_attachment={counts['message_attachment']}",
            )

        bad_message_source_imports = _scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM message_source ms
            JOIN message m ON m.id = ms.message_id
            WHERE NOT EXISTS (
                SELECT 1 FROM import_run ir WHERE ir.id = ms.import_run_id
            )
            """,
        )
        checks["message_sources_without_import"] = bad_message_source_imports
        if bad_message_source_imports:
            _issue(
                issues,
                "ERROR",
                "MESSAGE_SOURCE_IMPORT_TRACE_MISSING",
                f"{bad_message_source_imports} message_source row(s) have no import_run",
            )

    except sqlite3.Error as exc:
        _issue(issues, "ERROR", "A2_VALIDATION_QUERY_FAILED", str(exc))
    finally:
        conn.close()

    return _finalize(path, issues, counts, checks)


def _finalize(
    path: Path,
    issues: list[dict[str, Any]],
    counts: dict[str, int],
    checks: dict[str, Any],
) -> dict[str, Any]:
    errors = sum(1 for item in issues if item["severity"] == "ERROR")
    warnings = sum(1 for item in issues if item["severity"] == "WARNING")
    status = STATUS_FAIL if errors else STATUS_WARNING if warnings else STATUS_PASS
    fingerprints: dict[str, str] = {}
    if path.is_file():
        fingerprints["database_sha256"] = _sha256_file(path)
        wal_path = Path(str(path) + "-wal")
        if wal_path.is_file():
            fingerprints["wal_sha256"] = _sha256_file(wal_path)

    return {
        "schema_version": 1,
        "status": status,
        "database": str(path),
        "counts": {**counts, "errors": errors, "warnings": warnings},
        "checks": checks,
        "fingerprints": fingerprints,
        "issues": issues,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only A7 validator for an A2 SQLite database.")
    parser.add_argument("database", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    report = validate_sqlite_database(args.database)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    return 1 if report["status"] == STATUS_FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
