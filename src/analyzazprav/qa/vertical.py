from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from .jsonl import iter_physical_jsonl_lines
from .reconciliation import validate_staging_bundle
from .staging import STATUS_FAIL, STATUS_PASS, STATUS_WARNING

A2_AUTHORITATIVE_TABLES = (
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
)


def _issue(issues: list[dict[str, Any]], severity: str, code: str, detail: str) -> None:
    issues.append({"severity": severity, "code": code, "detail": detail})


def _load_bundle(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    records = [
        json.loads(line)
        for _, line in iter_physical_jsonl_lines(root / "messages.jsonl")
        if line.strip()
    ]
    if not isinstance(manifest, dict) or any(not isinstance(record, dict) for record in records):
        raise ValueError("invalid A1 staging bundle")
    return manifest, records


def _table_rows(conn: sqlite3.Connection, table: str) -> list[list[Any]]:
    columns = [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")]
    if not columns:
        return []
    quoted = ", ".join('"' + column.replace('"', '""') + '"' for column in columns)
    rows = conn.execute(f"SELECT {quoted} FROM {table} ORDER BY rowid").fetchall()
    result: list[list[Any]] = []
    for row in rows:
        values: list[Any] = []
        for value in row:
            if isinstance(value, bytes):
                values.append({"__bytes_hex__": value.hex()})
            else:
                values.append(value)
        result.append(values)
    return result


def canonical_fingerprint(
    conn: sqlite3.Connection,
    tables: Iterable[str] = A2_AUTHORITATIVE_TABLES,
) -> str:
    """Deterministic logical fingerprint of A2 authoritative tables."""

    present = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    payload: dict[str, Any] = {}
    for table in tables:
        if table in present:
            payload[table] = _table_rows(conn, table)
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _expected_relation_count(records: list[dict[str, Any]]) -> int:
    total = 0
    for record in records:
        relations = record.get("conversation_sources")
        if isinstance(relations, list) and relations:
            total += len(
                {
                    relation.get("source_conversation_key")
                    for relation in relations
                    if isinstance(relation, dict)
                    and isinstance(relation.get("source_conversation_key"), str)
                    and relation.get("source_conversation_key")
                }
            )
        elif record.get("conversation_source_id") not in (None, ""):
            total += 1
    return total


def _finalize(
    staging_dir: Path,
    database: Path,
    issues: list[dict[str, Any]],
    checks: dict[str, Any],
) -> dict[str, Any]:
    errors = sum(issue["severity"] == "ERROR" for issue in issues)
    warnings = sum(issue["severity"] == "WARNING" for issue in issues)
    status = STATUS_FAIL if errors else STATUS_WARNING if warnings else STATUS_PASS
    material = json.dumps(
        {"checks": checks, "issues": issues},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "schema_version": 2,
        "status": status,
        "staging_dir": str(staging_dir),
        "database": str(database),
        "checks": checks,
        "counts": {"errors": int(errors), "warnings": int(warnings)},
        "report_fingerprint_sha256": hashlib.sha256(material.encode("utf-8")).hexdigest(),
        "issues": issues,
    }


def validate_vertical_pipeline(staging_dir: str | Path, database: str | Path) -> dict[str, Any]:
    """Read-only reconciliation of the current A1→A2→A3 vertical slice."""

    staging_dir = Path(staging_dir)
    database = Path(database)
    issues: list[dict[str, Any]] = []
    checks: dict[str, Any] = {}

    staging_report = validate_staging_bundle(staging_dir)
    checks["a1_staging_status"] = staging_report["status"]
    checks["a1_record_count"] = staging_report["counts"]["records"]
    checks["a1_attachment_count"] = staging_report["counts"]["attachments"]
    checks["a1_conversation_relation_count"] = staging_report["counts"]["conversation_relations"]
    checks["a1_record_set_fingerprint"] = staging_report["fingerprints"]["record_set_sha256"]
    checks["a1_reconciliation_status"] = (staging_report.get("reconciliation") or {}).get("status")
    checks["a1_reconciliation_fingerprint"] = staging_report["fingerprints"].get("reconciliation_sha256")
    if staging_report["status"] == STATUS_FAIL:
        _issue(issues, "ERROR", "A1_STAGING_GATE_FAILED", "A1 staging/reconciliation validation failed")
        return _finalize(staging_dir, database, issues, checks)

    try:
        manifest, records = _load_bundle(staging_dir)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        _issue(issues, "ERROR", "A1_BUNDLE_LOAD_FAILED", str(exc))
        return _finalize(staging_dir, database, issues, checks)

    if not database.is_file():
        _issue(issues, "ERROR", "DATABASE_MISSING", f"database does not exist: {database}")
        return _finalize(staging_dir, database, issues, checks)

    source = manifest.get("source") or {}
    parser = manifest.get("parser") or {}
    source_type = source.get("type")
    source_sha256 = source.get("sha256")
    parser_version = parser.get("version")
    expected_keys = [str(record.get("source_record_key")) for record in records]
    expected_key_set = set(expected_keys)
    expected_attachments = [
        attachment
        for record in records
        for attachment in (record.get("attachments") or [])
        if isinstance(attachment, dict)
    ]
    expected_relation_count = _expected_relation_count(records)

    uri = database.resolve().as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        _issue(issues, "ERROR", "DATABASE_OPEN_FAILED", str(exc))
        return _finalize(staging_dir, database, issues, checks)
    conn.row_factory = sqlite3.Row

    try:
        conn.execute("PRAGMA query_only=ON")
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        checks["sqlite_integrity"] = integrity
        checks["foreign_key_error_count"] = len(fk_errors)
        if integrity != "ok":
            _issue(issues, "ERROR", "SQLITE_INTEGRITY_FAILED", integrity)
        if fk_errors:
            _issue(issues, "ERROR", "FOREIGN_KEY_ERRORS", f"{len(fk_errors)} foreign-key violation(s)")

        required_tables = {
            "import_run",
            "message_source",
            "message_conversation",
            "message_source_conversation",
            "attachment_source",
            "processing_run",
            "processed_message",
        }
        present_tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = sorted(required_tables - present_tables)
        if missing:
            _issue(issues, "ERROR", "VERTICAL_TABLES_MISSING", ", ".join(missing))
            return _finalize(staging_dir, database, issues, checks)

        import_rows = list(
            conn.execute(
                """SELECT id, status, parser_version, source_sha256
                   FROM import_run
                   WHERE source_type=? AND source_sha256=? AND parser_version=?
                   ORDER BY id""",
                (source_type, source_sha256, parser_version),
            )
        )
        checks["matching_import_runs"] = len(import_rows)
        if len(import_rows) != 1:
            _issue(
                issues,
                "ERROR",
                "A2_IMPORT_RUN_NOT_UNIQUE",
                f"expected exactly one A2 run for source snapshot/parser version, found {len(import_rows)}",
            )
            return _finalize(staging_dir, database, issues, checks)

        import_run = import_rows[0]
        import_run_id = int(import_run["id"])
        checks["a2_import_run_id"] = import_run_id
        checks["a2_import_status"] = import_run["status"]
        if import_run["status"] != "completed":
            _issue(issues, "ERROR", "A2_IMPORT_NOT_COMPLETED", f"status={import_run['status']!r}")

        source_rows = list(
            conn.execute(
                """SELECT id, message_id, source_record_key
                   FROM message_source
                   WHERE import_run_id=?
                   ORDER BY id""",
                (import_run_id,),
            )
        )
        actual_keys = [str(row["source_record_key"]) for row in source_rows if row["source_record_key"] is not None]
        actual_key_set = set(actual_keys)
        checks["a2_message_source_count"] = len(source_rows)
        checks["a2_unique_source_record_keys"] = len(actual_key_set)
        checks["a2_distinct_canonical_messages_for_import"] = len({int(row["message_id"]) for row in source_rows})
        if Counter(actual_keys) != Counter(expected_keys):
            missing_keys = sorted(expected_key_set - actual_key_set)
            extra_keys = sorted(actual_key_set - expected_key_set)
            _issue(
                issues,
                "ERROR",
                "A1_A2_SOURCE_RECORD_MISMATCH",
                f"missing={missing_keys[:5]}, extra={extra_keys[:5]}",
            )

        actual_attachment_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM attachment_source WHERE import_run_id=?",
                (import_run_id,),
            ).fetchone()[0]
        )
        checks["a2_attachment_source_count"] = actual_attachment_count
        if actual_attachment_count != len(expected_attachments):
            _issue(
                issues,
                "ERROR",
                "A1_A2_ATTACHMENT_COUNT_MISMATCH",
                f"A1={len(expected_attachments)}, A2={actual_attachment_count}",
            )

        actual_source_relations = list(
            conn.execute(
                """SELECT msc.membership_id, msc.message_source_id
                   FROM message_source_conversation msc
                   JOIN message_source ms ON ms.id=msc.message_source_id
                   WHERE ms.import_run_id=?
                   ORDER BY msc.message_source_id, msc.conversation_source_id""",
                (import_run_id,),
            )
        )
        checks["a2_source_conversation_relation_count"] = len(actual_source_relations)
        if len(actual_source_relations) != expected_relation_count:
            _issue(
                issues,
                "ERROR",
                "A1_A2_CONVERSATION_RELATION_MISMATCH",
                f"A1={expected_relation_count}, A2={len(actual_source_relations)}",
            )

        a2_membership_ids = {
            int(row[0]) for row in conn.execute("SELECT membership_id FROM analysis_messages")
        }
        a2_message_ids = {
            int(row[0]) for row in conn.execute("SELECT DISTINCT id FROM analysis_messages")
        }
        checks["a2_total_membership_count"] = len(a2_membership_ids)
        checks["a2_total_canonical_message_count"] = len(a2_message_ids)

        processing_rows = list(
            conn.execute(
                """SELECT id, status, processing_version, input_membership_count,
                          canonical_message_count, output_membership_count, config_json
                   FROM processing_run
                   WHERE status='completed'
                   ORDER BY id"""
            )
        )
        checks["completed_processing_run_count"] = len(processing_rows)
        if not processing_rows:
            _issue(issues, "ERROR", "A3_COMPLETED_RUN_MISSING", "no completed A3 processing run exists")
            return _finalize(staging_dir, database, issues, checks)

        latest = processing_rows[-1]
        processing_run_id = int(latest["id"])
        checks["a3_processing_run_id"] = processing_run_id
        checks["a3_processing_version"] = latest["processing_version"]
        checks["a3_processing_config"] = json.loads(latest["config_json"] or "{}")
        checks["a3_input_membership_count"] = int(latest["input_membership_count"])
        checks["a3_output_membership_count"] = int(latest["output_membership_count"])
        checks["a3_canonical_message_count"] = int(latest["canonical_message_count"])

        processed_rows = list(
            conn.execute(
                """SELECT membership_id, message_id, conversation_id
                   FROM processed_message
                   WHERE processing_run_id=?
                   ORDER BY membership_id""",
                (processing_run_id,),
            )
        )
        processed_membership_ids = {int(row["membership_id"]) for row in processed_rows}
        checks["a3_processed_membership_rows"] = len(processed_rows)

        if int(latest["input_membership_count"]) != len(a2_membership_ids):
            _issue(issues, "ERROR", "A2_A3_INPUT_MEMBERSHIP_COUNT_MISMATCH", f"A2={len(a2_membership_ids)}, A3={latest['input_membership_count']}")
        if int(latest["output_membership_count"]) != len(processed_rows):
            _issue(issues, "ERROR", "A3_OUTPUT_ACCOUNTING_MISMATCH", f"declared={latest['output_membership_count']}, rows={len(processed_rows)}")
        if int(latest["canonical_message_count"]) != len(a2_message_ids):
            _issue(issues, "ERROR", "A2_A3_CANONICAL_MESSAGE_COUNT_MISMATCH", f"A2={len(a2_message_ids)}, A3={latest['canonical_message_count']}")
        if processed_membership_ids != a2_membership_ids:
            missing_memberships = sorted(a2_membership_ids - processed_membership_ids)
            extra_memberships = sorted(processed_membership_ids - a2_membership_ids)
            _issue(
                issues,
                "ERROR",
                "A2_A3_MEMBERSHIP_SET_MISMATCH",
                f"missing={missing_memberships[:10]}, extra={extra_memberships[:10]}",
            )

        provenance_missing = int(
            conn.execute(
                """SELECT COUNT(*)
                   FROM processed_message pm
                   WHERE pm.processing_run_id=?
                     AND NOT EXISTS (
                         SELECT 1
                         FROM message_source_conversation msc
                         JOIN message_source ms ON ms.id=msc.message_source_id
                         WHERE msc.membership_id=pm.membership_id
                           AND ms.source_record_key IS NOT NULL
                     )""",
                (processing_run_id,),
            ).fetchone()[0]
        )
        checks["a3_memberships_without_source_record_provenance"] = provenance_missing
        if provenance_missing:
            _issue(
                issues,
                "ERROR",
                "A3_SOURCE_PROVENANCE_MISSING",
                f"{provenance_missing} processed membership(s) cannot resolve to source_record_key",
            )

        checks["a2_canonical_fingerprint"] = canonical_fingerprint(conn)

    except (sqlite3.Error, ValueError, TypeError, json.JSONDecodeError) as exc:
        _issue(issues, "ERROR", "VERTICAL_QUERY_FAILED", str(exc))
    finally:
        conn.close()

    return _finalize(staging_dir, database, issues, checks)
