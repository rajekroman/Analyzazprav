from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


STATUS_PASS = "PASS"
STATUS_WARNING = "WARNING"
STATUS_FAIL = "FAIL"
MIN_SCHEMA_VERSION = 5

REQUIRED_TABLES = {
    "schema_meta",
    "schema_migration",
    "import_run",
    "participant",
    "participant_identity",
    "conversation",
    "conversation_source",
    "conversation_participant",
    "message",
    "message_source",
    "message_conversation",
    "message_source_conversation",
    "message_relation",
    "attachment",
    "message_attachment",
    "message_attachment_occurrence",
    "attachment_source",
}
REQUIRED_VIEWS = {
    "analysis_messages",
    "analysis_conversations",
    "analysis_attachments",
    "analysis_message_sources",
    "analysis_message_memberships",
}


def _issue(issues: list[dict[str, Any]], severity: str, code: str, detail: str) -> None:
    issues.append({"severity": severity, "code": code, "detail": detail})


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def _parse_local_offset_minutes(value: str) -> int:
    text = value.strip()
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("local timestamp has no explicit UTC offset")
    seconds = parsed.utcoffset().total_seconds()
    if seconds % 60:
        raise ValueError("UTC offset is not an integer number of minutes")
    return int(seconds // 60)


def _schema_version(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
    if row is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


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
            "Database has a non-empty WAL; use a stable/checkpointed snapshot for reproducible byte hashing.",
        )

    uri = f"{path.resolve().as_uri()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        _issue(issues, "ERROR", "DATABASE_OPEN_FAILED", str(exc))
        return _finalize(path, issues, counts, checks)

    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")

        integrity_rows = [str(row[0]) for row in conn.execute("PRAGMA integrity_check")]
        checks["integrity_check"] = integrity_rows
        if integrity_rows != ["ok"]:
            _issue(
                issues,
                "ERROR",
                "SQLITE_INTEGRITY_FAILED",
                "; ".join(integrity_rows[:20]),
            )

        fk_rows = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check")]
        checks["foreign_key_errors"] = len(fk_rows)
        if fk_rows:
            _issue(
                issues,
                "ERROR",
                "SQLITE_FOREIGN_KEY_ERRORS",
                f"{len(fk_rows)} foreign-key violation(s) detected",
            )

        objects = {
            str(row["name"]): str(row["type"])
            for row in conn.execute(
                "SELECT name, type FROM sqlite_master WHERE type IN ('table','view')"
            )
        }
        present_tables = {name for name, kind in objects.items() if kind == "table"}
        present_views = {name for name, kind in objects.items() if kind == "view"}
        missing_tables = sorted(REQUIRED_TABLES - present_tables)
        missing_views = sorted(REQUIRED_VIEWS - present_views)
        if missing_tables:
            _issue(issues, "ERROR", "A2_REQUIRED_TABLES_MISSING", ", ".join(missing_tables))
        if missing_views:
            _issue(issues, "ERROR", "A2_REQUIRED_VIEWS_MISSING", ", ".join(missing_views))
        if missing_tables or missing_views:
            return _finalize(path, issues, counts, checks)

        schema_version = _schema_version(conn)
        checks["schema_version"] = schema_version
        if schema_version is None:
            _issue(issues, "ERROR", "A2_SCHEMA_VERSION_INVALID", "schema_meta.schema_version is missing or non-numeric")
        elif schema_version < MIN_SCHEMA_VERSION:
            _issue(
                issues,
                "ERROR",
                "A2_SCHEMA_VERSION_TOO_OLD",
                f"A7 requires A2 schema >= {MIN_SCHEMA_VERSION}; found {schema_version}",
            )

        for table in (
            "import_run",
            "conversation",
            "conversation_source",
            "participant",
            "message",
            "message_source",
            "message_conversation",
            "message_source_conversation",
            "attachment",
            "attachment_source",
            "message_attachment",
            "message_attachment_occurrence",
        ):
            counts[table] = _scalar(conn, f"SELECT COUNT(*) FROM {table}")

        messages_without_source = _scalar(
            conn,
            """SELECT COUNT(*) FROM message m
               WHERE NOT EXISTS (SELECT 1 FROM message_source ms WHERE ms.message_id=m.id)""",
        )
        checks["messages_without_source"] = messages_without_source
        if messages_without_source:
            _issue(
                issues,
                "ERROR",
                "MESSAGE_SOURCE_TRACE_MISSING",
                f"{messages_without_source} canonical message(s) have no message_source provenance",
            )

        messages_without_membership = _scalar(
            conn,
            """SELECT COUNT(*) FROM message m
               WHERE NOT EXISTS (SELECT 1 FROM message_conversation mc WHERE mc.message_id=m.id)""",
        )
        checks["messages_without_membership"] = messages_without_membership
        if messages_without_membership:
            _issue(
                issues,
                "ERROR",
                "MESSAGE_CONVERSATION_MEMBERSHIP_MISSING",
                f"{messages_without_membership} canonical message(s) have no conversation membership",
            )

        bad_primary_membership = _scalar(
            conn,
            """SELECT COUNT(*) FROM (
                   SELECT m.id, SUM(CASE WHEN mc.is_primary=1 THEN 1 ELSE 0 END) AS primary_count
                   FROM message m LEFT JOIN message_conversation mc ON mc.message_id=m.id
                   GROUP BY m.id HAVING primary_count <> 1
               )""",
        )
        checks["messages_without_exactly_one_primary_membership"] = bad_primary_membership
        if bad_primary_membership:
            _issue(
                issues,
                "ERROR",
                "MESSAGE_PRIMARY_MEMBERSHIP_INVALID",
                f"{bad_primary_membership} message(s) do not have exactly one primary membership",
            )

        primary_pointer_mismatches = _scalar(
            conn,
            """SELECT COUNT(*)
               FROM message m
               JOIN message_conversation mc ON mc.message_id=m.id AND mc.is_primary=1
               WHERE mc.conversation_id <> m.conversation_id""",
        )
        checks["primary_membership_pointer_mismatches"] = primary_pointer_mismatches
        if primary_pointer_mismatches:
            _issue(
                issues,
                "ERROR",
                "MESSAGE_PRIMARY_CONVERSATION_MISMATCH",
                f"{primary_pointer_mismatches} message.conversation_id value(s) disagree with the primary membership",
            )

        duplicate_source_keys = _scalar(
            conn,
            """SELECT COUNT(*) FROM (
                   SELECT import_run_id, source_record_key
                   FROM message_source
                   WHERE source_record_key IS NOT NULL AND trim(source_record_key) <> ''
                   GROUP BY import_run_id, source_record_key HAVING COUNT(*) > 1
               )""",
        )
        checks["duplicate_source_record_keys_within_import"] = duplicate_source_keys
        if duplicate_source_keys:
            _issue(
                issues,
                "ERROR",
                "SOURCE_RECORD_KEY_DUPLICATE_IN_A2",
                f"{duplicate_source_keys} duplicated source_record_key group(s) detected within an import run",
            )

        keyed_sources_without_relation = _scalar(
            conn,
            """SELECT COUNT(*) FROM message_source ms
               WHERE ms.source_record_key IS NOT NULL AND trim(ms.source_record_key) <> ''
                 AND NOT EXISTS (
                    SELECT 1 FROM message_source_conversation msc
                    WHERE msc.message_source_id=ms.id
                 )""",
        )
        checks["keyed_message_sources_without_conversation_relation"] = keyed_sources_without_relation
        if keyed_sources_without_relation:
            _issue(
                issues,
                "ERROR",
                "MESSAGE_SOURCE_CONVERSATION_TRACE_MISSING",
                f"{keyed_sources_without_relation} keyed message_source row(s) have no source conversation relation",
            )

        empty_snapshot_keys = _scalar(
            conn,
            "SELECT COUNT(*) FROM conversation_source WHERE source_snapshot_key IS NULL OR trim(source_snapshot_key)=''",
        )
        checks["empty_source_snapshot_keys"] = empty_snapshot_keys
        if empty_snapshot_keys:
            _issue(
                issues,
                "ERROR",
                "CONVERSATION_SOURCE_SNAPSHOT_MISSING",
                f"{empty_snapshot_keys} conversation_source row(s) have no immutable snapshot key",
            )

        empty_source_hashes = _scalar(
            conn,
            "SELECT COUNT(*) FROM message_source WHERE source_hash IS NULL OR trim(source_hash)=''",
        )
        checks["empty_source_hashes"] = empty_source_hashes
        if empty_source_hashes:
            _issue(
                issues,
                "ERROR",
                "MESSAGE_SOURCE_HASH_MISSING",
                f"{empty_source_hashes} message_source row(s) have an empty source_hash",
            )

        attachments_without_source = _scalar(
            conn,
            """SELECT COUNT(*) FROM attachment a
               WHERE NOT EXISTS (SELECT 1 FROM attachment_source s WHERE s.attachment_id=a.id)""",
        )
        checks["attachments_without_source"] = attachments_without_source
        if attachments_without_source:
            _issue(
                issues,
                "ERROR",
                "ATTACHMENT_SOURCE_TRACE_MISSING",
                f"{attachments_without_source} canonical attachment(s) have no attachment_source provenance",
            )

        attachments_without_occurrence = _scalar(
            conn,
            """SELECT COUNT(*) FROM attachment a
               WHERE NOT EXISTS (
                    SELECT 1 FROM message_attachment_occurrence mao WHERE mao.attachment_id=a.id
               )""",
        )
        checks["attachments_without_occurrence"] = attachments_without_occurrence
        if attachments_without_occurrence:
            _issue(
                issues,
                "ERROR",
                "ATTACHMENT_OCCURRENCE_MISSING",
                f"{attachments_without_occurrence} canonical attachment(s) have no message occurrence",
            )

        keyed_attachment_sources_without_occurrence = _scalar(
            conn,
            """SELECT COUNT(*) FROM attachment_source
               WHERE source_occurrence_key IS NOT NULL AND trim(source_occurrence_key) <> ''
                 AND message_attachment_occurrence_id IS NULL""",
        )
        checks["keyed_attachment_sources_without_occurrence"] = keyed_attachment_sources_without_occurrence
        if keyed_attachment_sources_without_occurrence:
            _issue(
                issues,
                "ERROR",
                "ATTACHMENT_SOURCE_OCCURRENCE_TRACE_MISSING",
                f"{keyed_attachment_sources_without_occurrence} keyed attachment source row(s) have no occurrence link",
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

        statistics_mismatches = 0
        for row in conn.execute("SELECT id, statistics_json FROM import_run WHERE status='completed'"):
            try:
                stats = json.loads(row["statistics_json"] or "{}")
            except json.JSONDecodeError:
                _issue(
                    issues,
                    "ERROR",
                    "IMPORT_STATISTICS_JSON_INVALID",
                    f"import_run {row['id']} has invalid statistics_json",
                )
                statistics_mismatches += 1
                continue
            if not isinstance(stats, dict):
                continue
            actual = {
                "messages": _scalar(conn, "SELECT COUNT(*) FROM message_source WHERE import_run_id=?", (int(row["id"]),)),
                "attachments": _scalar(conn, "SELECT COUNT(*) FROM attachment_source WHERE import_run_id=?", (int(row["id"]),)),
                "conversation_relations": _scalar(
                    conn,
                    """SELECT COUNT(*) FROM message_source_conversation msc
                       JOIN message_source ms ON ms.id=msc.message_source_id
                       WHERE ms.import_run_id=?""",
                    (int(row["id"]),),
                ),
            }
            for key, actual_value in actual.items():
                if key in stats and isinstance(stats[key], int) and not isinstance(stats[key], bool):
                    if int(stats[key]) != actual_value:
                        statistics_mismatches += 1
                        _issue(
                            issues,
                            "ERROR",
                            "IMPORT_STATISTICS_COUNT_MISMATCH",
                            f"import_run {row['id']} {key}={stats[key]} but canonical provenance contains {actual_value}",
                        )
        checks["import_statistics_mismatches"] = statistics_mismatches

        unknown_senders = _scalar(conn, "SELECT COUNT(*) FROM message WHERE sender_id IS NULL")
        checks["messages_without_sender"] = unknown_senders
        if unknown_senders:
            _issue(
                issues,
                "WARNING",
                "MESSAGE_SENDER_UNKNOWN",
                f"{unknown_senders} canonical message(s) have no sender_id",
            )

        bad_timezone_offsets = _scalar(
            conn,
            "SELECT COUNT(*) FROM message WHERE timezone_offset_min IS NOT NULL AND (timezone_offset_min < -840 OR timezone_offset_min > 840)",
        )
        checks["timezone_offsets_out_of_range"] = bad_timezone_offsets
        if bad_timezone_offsets:
            _issue(
                issues,
                "ERROR",
                "TIMEZONE_OFFSET_OUT_OF_RANGE",
                f"{bad_timezone_offsets} message(s) have timezone_offset_min outside [-840, 840]",
            )

        invalid_local_times = 0
        local_offset_mismatches = 0
        for row in conn.execute(
            "SELECT id, sent_at_local_iso, timezone_offset_min FROM message WHERE sent_at_local_iso IS NOT NULL"
        ):
            try:
                local_offset = _parse_local_offset_minutes(str(row["sent_at_local_iso"]))
            except (TypeError, ValueError) as exc:
                invalid_local_times += 1
                _issue(
                    issues,
                    "ERROR",
                    "LOCAL_TIMESTAMP_INVALID",
                    f"message {row['id']}: {exc}",
                )
                continue
            explicit_offset = row["timezone_offset_min"]
            if explicit_offset is not None and int(explicit_offset) != local_offset:
                local_offset_mismatches += 1
                _issue(
                    issues,
                    "ERROR",
                    "LOCAL_TIMESTAMP_OFFSET_MISMATCH",
                    f"message {row['id']} local timestamp offset={local_offset}, timezone_offset_min={explicit_offset}",
                )
        checks["invalid_local_timestamps"] = invalid_local_times
        checks["local_timestamp_offset_mismatches"] = local_offset_mismatches

        analysis_counts = {
            "analysis_messages": _scalar(conn, "SELECT COUNT(*) FROM analysis_messages"),
            "analysis_conversations": _scalar(conn, "SELECT COUNT(*) FROM analysis_conversations"),
            "analysis_attachments": _scalar(conn, "SELECT COUNT(*) FROM analysis_attachments"),
            "analysis_message_sources": _scalar(conn, "SELECT COUNT(*) FROM analysis_message_sources"),
            "analysis_message_memberships_distinct": _scalar(
                conn, "SELECT COUNT(DISTINCT membership_id) FROM analysis_message_memberships"
            ),
            "analysis_message_memberships_source_relations": _scalar(
                conn, "SELECT COUNT(*) FROM analysis_message_memberships WHERE message_source_id IS NOT NULL"
            ),
        }
        checks.update(analysis_counts)
        expected_projection_counts = {
            "analysis_messages": counts["message_conversation"],
            "analysis_conversations": counts["conversation"],
            "analysis_attachments": counts["message_attachment_occurrence"],
            "analysis_message_sources": counts["message_source"],
            "analysis_message_memberships_distinct": counts["message_conversation"],
            "analysis_message_memberships_source_relations": counts["message_source_conversation"],
        }
        for key, expected in expected_projection_counts.items():
            actual = int(analysis_counts[key])
            if actual != expected:
                _issue(
                    issues,
                    "ERROR",
                    "ANALYTICAL_VIEW_COUNT_MISMATCH",
                    f"{key}={actual}, expected {expected} from canonical v5 tables",
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
        "schema_version": 2,
        "status": status,
        "database": str(path),
        "counts": {**counts, "errors": errors, "warnings": warnings},
        "checks": checks,
        "fingerprints": fingerprints,
        "issues": issues,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only A7 validator for A2 v5 SQLite.")
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
