from __future__ import annotations

import argparse
from collections import Counter
import json
import sqlite3
from pathlib import Path
from typing import Any

from .staging_validator import STATUS_FAIL as STAGING_FAIL
from .staging_validator import validate_staging_dir


STATUS_PASS = "PASS"
STATUS_WARNING = "WARNING"
STATUS_FAIL = "FAIL"


def _issue(issues: list[dict[str, Any]], severity: str, code: str, detail: str) -> None:
    issues.append({"severity": severity, "code": code, "detail": detail})


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _load_staging(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = []
    with (root / "messages.jsonl").open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    records.append(value)
    return manifest, records


def reconcile_a1_a2(staging_dir: str | Path, database: str | Path) -> dict[str, Any]:
    staging_dir = Path(staging_dir)
    database = Path(database)
    issues: list[dict[str, Any]] = []
    checks: dict[str, Any] = {}

    staging_report = validate_staging_dir(staging_dir)
    checks["a1_staging_status"] = staging_report["status"]
    if staging_report["status"] == STAGING_FAIL:
        _issue(
            issues,
            "ERROR",
            "A1_GATE_FAILED",
            "A1 staging validation failed; exact A1→A2 reconciliation is not trustworthy.",
        )
        return _finalize(staging_dir, database, issues, checks)

    if not database.is_file():
        _issue(issues, "ERROR", "DATABASE_MISSING", f"SQLite database not found: {database}")
        return _finalize(staging_dir, database, issues, checks)

    try:
        manifest, records = _load_staging(staging_dir)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        _issue(issues, "ERROR", "A1_LOAD_FAILED", str(exc))
        return _finalize(staging_dir, database, issues, checks)

    source = manifest.get("source") if isinstance(manifest, dict) else None
    if not isinstance(source, dict):
        _issue(issues, "ERROR", "A1_SOURCE_MANIFEST_MISSING", "manifest.source is required")
        return _finalize(staging_dir, database, issues, checks)

    source_type = source.get("type")
    source_fingerprint = source.get("sha256")
    if not isinstance(source_type, str) or not isinstance(source_fingerprint, str):
        _issue(
            issues,
            "ERROR",
            "A1_SOURCE_IDENTITY_INCOMPLETE",
            "manifest.source.type and manifest.source.sha256 are required",
        )
        return _finalize(staging_dir, database, issues, checks)

    expected_keys = [str(record["source_record_key"]) for record in records]
    expected_key_set = set(expected_keys)
    expected_attachments = Counter(
        str(attachment["source_attachment_id"])
        for record in records
        for attachment in (record.get("attachments") or [])
        if isinstance(attachment, dict) and attachment.get("source_attachment_id") not in (None, "")
    )

    uri = f"{database.resolve().as_uri()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        _issue(issues, "ERROR", "DATABASE_OPEN_FAILED", str(exc))
        return _finalize(staging_dir, database, issues, checks)

    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        required_tables = {"import_run", "message", "message_source", "attachment_source"}
        present_tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing_tables = sorted(required_tables - present_tables)
        if missing_tables:
            _issue(
                issues,
                "ERROR",
                "A2_RECONCILIATION_TABLES_MISSING",
                ", ".join(missing_tables),
            )
            return _finalize(staging_dir, database, issues, checks)

        message_source_columns = _columns(conn, "message_source")
        if "source_record_key" not in message_source_columns:
            _issue(
                issues,
                "ERROR",
                "A2_SOURCE_RECORD_KEY_COLUMN_MISSING",
                "message_source must preserve A1 source_record_key verbatim for exact reconciliation",
            )
            return _finalize(staging_dir, database, issues, checks)

        import_rows = list(
            conn.execute(
                """
                SELECT id, status
                FROM import_run
                WHERE source_type=? AND source_fingerprint=?
                """,
                (source_type, source_fingerprint),
            )
        )
        checks["matching_import_runs"] = len(import_rows)
        if not import_rows:
            _issue(
                issues,
                "ERROR",
                "A2_IMPORT_RUN_NOT_FOUND",
                "No A2 import_run matches A1 source type and SHA-256 fingerprint",
            )
            return _finalize(staging_dir, database, issues, checks)
        if len(import_rows) != 1:
            _issue(
                issues,
                "ERROR",
                "A2_IMPORT_RUN_AMBIGUOUS",
                f"Expected one matching import_run; found {len(import_rows)}",
            )
            return _finalize(staging_dir, database, issues, checks)

        import_run_id = int(import_rows[0]["id"])
        checks["import_run_id"] = import_run_id
        checks["import_run_status"] = import_rows[0]["status"]
        if import_rows[0]["status"] != "completed":
            _issue(
                issues,
                "WARNING",
                "A2_IMPORT_NOT_COMPLETED",
                f"Matching import_run status is {import_rows[0]['status']!r}",
            )

        source_rows = list(
            conn.execute(
                """
                SELECT ms.source_record_key, ms.message_id
                FROM message_source ms
                WHERE ms.import_run_id=?
                """,
                (import_run_id,),
            )
        )
        actual_keys = [str(row["source_record_key"]) for row in source_rows if row["source_record_key"] is not None]
        actual_key_set = set(actual_keys)

        checks["a1_message_count"] = len(records)
        checks["a2_message_source_count"] = len(source_rows)
        checks["a1_unique_source_keys"] = len(expected_key_set)
        checks["a2_unique_source_keys"] = len(actual_key_set)
        checks["a2_distinct_canonical_messages"] = len({int(row["message_id"]) for row in source_rows})

        duplicate_a2_keys = [key for key, count in Counter(actual_keys).items() if count > 1]
        if duplicate_a2_keys:
            _issue(
                issues,
                "ERROR",
                "A2_SOURCE_RECORD_KEY_DUPLICATE",
                f"A2 contains {len(duplicate_a2_keys)} duplicated source_record_key value(s)",
            )

        missing_keys = sorted(expected_key_set - actual_key_set)
        extra_keys = sorted(actual_key_set - expected_key_set)
        checks["messages_missing_in_a2"] = len(missing_keys)
        checks["messages_extra_in_a2"] = len(extra_keys)
        if missing_keys:
            _issue(
                issues,
                "ERROR",
                "A1_MESSAGES_MISSING_IN_A2",
                f"{len(missing_keys)} A1 source record(s) are absent from A2; examples: {missing_keys[:5]}",
            )
        if extra_keys:
            _issue(
                issues,
                "ERROR",
                "A2_MESSAGES_NOT_IN_A1",
                f"{len(extra_keys)} A2 source record(s) are not present in this A1 staging set; examples: {extra_keys[:5]}",
            )

        attachment_columns = _columns(conn, "attachment_source")
        if "source_attachment_id" not in attachment_columns:
            _issue(
                issues,
                "ERROR",
                "A2_ATTACHMENT_SOURCE_ID_COLUMN_MISSING",
                "attachment_source.source_attachment_id is required for attachment reconciliation",
            )
        else:
            actual_attachment_rows = list(
                conn.execute(
                    """
                    SELECT source_attachment_id
                    FROM attachment_source
                    WHERE import_run_id=? AND source_attachment_id IS NOT NULL
                    """,
                    (import_run_id,),
                )
            )
            actual_attachments = Counter(str(row["source_attachment_id"]) for row in actual_attachment_rows)
            missing_attachments = expected_attachments - actual_attachments
            extra_attachments = actual_attachments - expected_attachments
            checks["a1_attachment_source_count"] = sum(expected_attachments.values())
            checks["a2_attachment_source_count"] = sum(actual_attachments.values())
            checks["attachments_missing_in_a2"] = sum(missing_attachments.values())
            checks["attachments_extra_in_a2"] = sum(extra_attachments.values())
            if missing_attachments:
                _issue(
                    issues,
                    "ERROR",
                    "A1_ATTACHMENTS_MISSING_IN_A2",
                    f"{sum(missing_attachments.values())} A1 attachment source record(s) are absent from A2",
                )
            if extra_attachments:
                _issue(
                    issues,
                    "ERROR",
                    "A2_ATTACHMENTS_NOT_IN_A1",
                    f"{sum(extra_attachments.values())} A2 attachment source record(s) are not present in this A1 staging set",
                )

    except sqlite3.Error as exc:
        _issue(issues, "ERROR", "A1_A2_RECONCILIATION_QUERY_FAILED", str(exc))
    finally:
        conn.close()

    return _finalize(staging_dir, database, issues, checks)


def _finalize(
    staging_dir: Path,
    database: Path,
    issues: list[dict[str, Any]],
    checks: dict[str, Any],
) -> dict[str, Any]:
    errors = sum(1 for issue in issues if issue["severity"] == "ERROR")
    warnings = sum(1 for issue in issues if issue["severity"] == "WARNING")
    status = STATUS_FAIL if errors else STATUS_WARNING if warnings else STATUS_PASS
    return {
        "schema_version": 1,
        "status": status,
        "staging_dir": str(staging_dir),
        "database": str(database),
        "checks": checks,
        "counts": {"errors": errors, "warnings": warnings},
        "issues": issues,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Exact read-only A1→A2 provenance reconciliation.")
    parser.add_argument("staging_dir", type=Path)
    parser.add_argument("database", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    report = reconcile_a1_a2(args.staging_dir, args.database)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    return 1 if report["status"] == STATUS_FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
