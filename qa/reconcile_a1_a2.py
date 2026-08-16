from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from .staging_validator import STATUS_FAIL as STAGING_FAIL, validate_staging_dir

STATUS_PASS = "PASS"
STATUS_WARNING = "WARNING"
STATUS_FAIL = "FAIL"
MIN_SCHEMA_VERSION = 5


def _issue(issues: list[dict[str, Any]], severity: str, code: str, detail: str) -> None:
    issues.append({"severity": severity, "code": code, "detail": detail})


def _source_fingerprint(manifest: Mapping[str, Any]) -> str:
    source = manifest.get("source") or {}
    parser = manifest.get("parser") or {}
    payload = {
        "contract_version": str(manifest.get("contract_version", "")),
        "source_type": source.get("type") if isinstance(source, Mapping) else None,
        "source_sha256": source.get("sha256") if isinstance(source, Mapping) else None,
        "parser_name": parser.get("name") if isinstance(parser, Mapping) else None,
        "parser_version": parser.get("version") if isinstance(parser, Mapping) else None,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _load(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    outputs = manifest.get("outputs")
    messages = root / str(outputs.get("messages") if isinstance(outputs, Mapping) and outputs.get("messages") else "messages.jsonl")
    rows: list[dict[str, Any]] = []
    with messages.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"messages line {line_no} is not an object")
            rows.append(row)
    return manifest, rows


def _conversation_ids(record: Mapping[str, Any]) -> list[str]:
    raw = record.get("conversation_sources")
    ids: list[str] = []
    if raw is not None:
        if not isinstance(raw, list):
            raise ValueError("conversation_sources must be an array")
        for item in raw:
            if isinstance(item, str):
                value = item.strip()
            elif isinstance(item, Mapping):
                candidate = item.get("source_conversation_key") or item.get("conversation_source_id") or item.get("source_conversation_id")
                if candidate is None and item.get("chat_guid") is not None:
                    candidate = f"guid:{item['chat_guid']}"
                if candidate is None and item.get("raw_chat_rowid") is not None:
                    candidate = f"rowid:{item['raw_chat_rowid']}"
                value = "" if candidate is None else str(candidate).strip()
            else:
                raise ValueError("invalid conversation_sources entry")
            if value and value not in ids:
                ids.append(value)
    legacy = record.get("conversation_source_id")
    if not ids and legacy not in (None, ""):
        ids.append(str(legacy).strip())
    if not ids:
        raise ValueError("message has no source conversation relation")
    return ids


def _expected_relations(records: list[dict[str, Any]]) -> Counter[tuple[str, str]]:
    result: Counter[tuple[str, str]] = Counter()
    for record in records:
        key = str(record["source_record_key"])
        for conversation_id in _conversation_ids(record):
            result[(key, conversation_id)] += 1
    return result


def _expected_attachment_occurrences(records: list[dict[str, Any]]) -> Counter[str]:
    result: Counter[str] = Counter()
    for record in records:
        key = str(record["source_record_key"])
        attachments = record.get("attachments") or []
        if not isinstance(attachments, list):
            raise ValueError("attachments must be an array")
        for position, attachment in enumerate(attachments):
            if not isinstance(attachment, Mapping):
                raise ValueError("attachment must be an object")
            source_id = attachment.get("source_attachment_id")
            identity = source_id if source_id is not None else position
            result[f"{key}:attachment:{identity}:position:{position}"] += 1
    return result


def _schema_version(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
    try:
        return int(row[0]) if row else None
    except (TypeError, ValueError):
        return None


def reconcile_a1_a2(staging_dir: str | Path, database: str | Path) -> dict[str, Any]:
    staging_dir, database = Path(staging_dir), Path(database)
    issues: list[dict[str, Any]] = []
    checks: dict[str, Any] = {}

    staging = validate_staging_dir(staging_dir)
    checks["a1_staging_status"] = staging["status"]
    if staging["status"] == STAGING_FAIL:
        _issue(issues, "ERROR", "A1_GATE_FAILED", "A1 staging validation failed")
        return _finalize(staging_dir, database, issues, checks)
    if not database.is_file():
        _issue(issues, "ERROR", "DATABASE_MISSING", f"SQLite database not found: {database}")
        return _finalize(staging_dir, database, issues, checks)

    try:
        manifest, records = _load(staging_dir)
        source = manifest.get("source") or {}
        parser = manifest.get("parser") or {}
        if not isinstance(source, Mapping) or not isinstance(parser, Mapping):
            raise ValueError("manifest source/parser must be objects")
        source_type, source_sha = source.get("type"), source.get("sha256")
        if not isinstance(source_type, str) or not isinstance(source_sha, str):
            raise ValueError("source.type and source.sha256 are required")
        parser_version = str(parser.get("version") or "") or None
        contract_version = str(manifest.get("contract_version") or "")
        fingerprint = _source_fingerprint(manifest)
        expected_keys = Counter(str(row["source_record_key"]) for row in records)
        expected_relations = _expected_relations(records)
        expected_attachments = _expected_attachment_occurrences(records)
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        _issue(issues, "ERROR", "A1_LOAD_FAILED", str(exc))
        return _finalize(staging_dir, database, issues, checks)

    uri = f"{database.resolve().as_uri()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        integrity = [str(row[0]) for row in conn.execute("PRAGMA integrity_check")]
        fk = list(conn.execute("PRAGMA foreign_key_check"))
        checks["integrity_check"] = integrity
        checks["foreign_key_errors"] = len(fk)
        if integrity != ["ok"]:
            _issue(issues, "ERROR", "A2_SQLITE_INTEGRITY_FAILED", "; ".join(integrity[:10]))
        if fk:
            _issue(issues, "ERROR", "A2_FOREIGN_KEY_ERRORS", f"{len(fk)} foreign-key violation(s)")

        schema_version = _schema_version(conn)
        checks["schema_version"] = schema_version
        if schema_version is None or schema_version < MIN_SCHEMA_VERSION:
            _issue(issues, "ERROR", "A2_SCHEMA_VERSION_TOO_OLD", f"A7 requires schema >= {MIN_SCHEMA_VERSION}; found {schema_version}")
            return _finalize(staging_dir, database, issues, checks)

        required = {"import_run", "message_source", "conversation_source", "message_source_conversation", "attachment_source"}
        tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = sorted(required - tables)
        if missing:
            _issue(issues, "ERROR", "A2_RECONCILIATION_TABLES_MISSING", ", ".join(missing))
            return _finalize(staging_dir, database, issues, checks)

        import_rows = list(
            conn.execute(
                """SELECT id,status,source_fingerprint,source_sha256,parser_version,statistics_json
                   FROM import_run WHERE source_type=? AND source_fingerprint=?""",
                (source_type, fingerprint),
            )
        )
        checks["expected_source_fingerprint"] = fingerprint
        checks["matching_import_runs"] = len(import_rows)
        if len(import_rows) != 1:
            _issue(issues, "ERROR", "A2_IMPORT_RUN_NOT_UNIQUE", f"expected one exact import_run; found {len(import_rows)}")
            return _finalize(staging_dir, database, issues, checks)
        run = import_rows[0]
        run_id = int(run["id"])
        checks["import_run_id"] = run_id
        checks["import_run_status"] = run["status"]
        if run["status"] != "completed":
            _issue(issues, "ERROR", "A2_IMPORT_NOT_COMPLETED", f"status={run['status']!r}")
        if run["source_sha256"] != source_sha:
            _issue(issues, "ERROR", "A2_SOURCE_SHA256_MISMATCH", "import_run source_sha256 differs from A1")
        if parser_version and run["parser_version"] != parser_version:
            _issue(issues, "ERROR", "A2_PARSER_VERSION_MISMATCH", f"A2={run['parser_version']!r}; A1={parser_version!r}")

        source_rows = list(
            conn.execute(
                """SELECT source_record_key,source_contract_version,source_type,message_id
                   FROM message_source WHERE import_run_id=?""",
                (run_id,),
            )
        )
        actual_keys = Counter(str(row["source_record_key"]) for row in source_rows if row["source_record_key"] is not None)
        missing_keys, extra_keys = expected_keys - actual_keys, actual_keys - expected_keys
        checks.update({
            "a1_message_count": sum(expected_keys.values()),
            "a2_message_source_count": sum(actual_keys.values()),
            "messages_missing_in_a2": sum(missing_keys.values()),
            "messages_extra_in_a2": sum(extra_keys.values()),
            "a2_distinct_canonical_messages": len({int(row["message_id"]) for row in source_rows}),
        })
        if missing_keys:
            _issue(issues, "ERROR", "A1_MESSAGES_MISSING_IN_A2", f"{sum(missing_keys.values())} source record(s) missing")
        if extra_keys:
            _issue(issues, "ERROR", "A2_MESSAGES_NOT_IN_A1", f"{sum(extra_keys.values())} unexpected source record(s)")
        bad_contract = sum(row["source_contract_version"] != contract_version for row in source_rows)
        bad_type = sum(row["source_type"] != source_type for row in source_rows)
        checks["message_sources_wrong_contract_version"] = bad_contract
        checks["message_sources_wrong_source_type"] = bad_type
        if bad_contract:
            _issue(issues, "ERROR", "A2_SOURCE_CONTRACT_VERSION_MISMATCH", f"{bad_contract} source row(s)")
        if bad_type:
            _issue(issues, "ERROR", "A2_SOURCE_TYPE_MISMATCH", f"{bad_type} source row(s)")

        relation_rows = list(
            conn.execute(
                """SELECT ms.source_record_key,cs.source_conversation_id,cs.source_snapshot_key,cs.source_sha256
                   FROM message_source ms
                   JOIN message_source_conversation msc ON msc.message_source_id=ms.id
                   JOIN conversation_source cs ON cs.id=msc.conversation_source_id
                   WHERE ms.import_run_id=?""",
                (run_id,),
            )
        )
        actual_relations = Counter((str(row["source_record_key"]), str(row["source_conversation_id"])) for row in relation_rows)
        missing_rel, extra_rel = expected_relations - actual_relations, actual_relations - expected_relations
        checks.update({
            "a1_conversation_relation_count": sum(expected_relations.values()),
            "a2_conversation_relation_count": sum(actual_relations.values()),
            "conversation_relations_missing_in_a2": sum(missing_rel.values()),
            "conversation_relations_extra_in_a2": sum(extra_rel.values()),
        })
        if missing_rel:
            _issue(issues, "ERROR", "A1_CONVERSATION_RELATIONS_MISSING_IN_A2", f"{sum(missing_rel.values())} relation(s) missing")
        if extra_rel:
            _issue(issues, "ERROR", "A2_CONVERSATION_RELATIONS_NOT_IN_A1", f"{sum(extra_rel.values())} unexpected relation(s)")
        wrong_snapshot = sum(row["source_snapshot_key"] != source_sha or row["source_sha256"] != source_sha for row in relation_rows)
        checks["conversation_relations_wrong_snapshot"] = wrong_snapshot
        if wrong_snapshot:
            _issue(issues, "ERROR", "A2_CONVERSATION_SNAPSHOT_MISMATCH", f"{wrong_snapshot} relation(s) use wrong source snapshot")

        attachment_rows = list(
            conn.execute(
                """SELECT source_occurrence_key,message_attachment_occurrence_id
                   FROM attachment_source WHERE import_run_id=?""",
                (run_id,),
            )
        )
        actual_attachments = Counter(str(row["source_occurrence_key"]) for row in attachment_rows if row["source_occurrence_key"] is not None)
        missing_att, extra_att = expected_attachments - actual_attachments, actual_attachments - expected_attachments
        checks.update({
            "a1_attachment_occurrence_count": sum(expected_attachments.values()),
            "a2_attachment_occurrence_count": sum(actual_attachments.values()),
            "attachments_missing_in_a2": sum(missing_att.values()),
            "attachments_extra_in_a2": sum(extra_att.values()),
        })
        if missing_att:
            _issue(issues, "ERROR", "A1_ATTACHMENTS_MISSING_IN_A2", f"{sum(missing_att.values())} occurrence(s) missing")
        if extra_att:
            _issue(issues, "ERROR", "A2_ATTACHMENTS_NOT_IN_A1", f"{sum(extra_att.values())} unexpected occurrence(s)")
        unlinked = sum(row["message_attachment_occurrence_id"] is None for row in attachment_rows)
        checks["attachment_sources_without_occurrence_link"] = unlinked
        if unlinked:
            _issue(issues, "ERROR", "A2_ATTACHMENT_OCCURRENCE_LINK_MISSING", f"{unlinked} attachment source row(s) unlinked")

        try:
            statistics = json.loads(run["statistics_json"] or "{}")
        except json.JSONDecodeError:
            statistics = {}
            _issue(issues, "ERROR", "A2_IMPORT_STATISTICS_INVALID", "statistics_json is invalid")
        expected_stats = {
            "messages": sum(expected_keys.values()),
            "attachments": sum(expected_attachments.values()),
            "conversation_relations": sum(expected_relations.values()),
        }
        for name, expected in expected_stats.items():
            if isinstance(statistics, Mapping) and name in statistics and statistics[name] != expected:
                _issue(issues, "ERROR", "A2_IMPORT_STATISTICS_MISMATCH", f"{name}: A2={statistics[name]!r}; expected={expected}")

    except sqlite3.Error as exc:
        _issue(issues, "ERROR", "A1_A2_RECONCILIATION_QUERY_FAILED", str(exc))
    finally:
        try:
            conn.close()
        except UnboundLocalError:
            pass

    return _finalize(staging_dir, database, issues, checks)


def _finalize(staging: Path, database: Path, issues: list[dict[str, Any]], checks: dict[str, Any]) -> dict[str, Any]:
    errors = sum(row["severity"] == "ERROR" for row in issues)
    warnings = sum(row["severity"] == "WARNING" for row in issues)
    return {
        "schema_version": 2,
        "status": STATUS_FAIL if errors else STATUS_WARNING if warnings else STATUS_PASS,
        "staging_dir": str(staging),
        "database": str(database),
        "checks": checks,
        "counts": {"errors": errors, "warnings": warnings},
        "issues": issues,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Exact A7 A1→A2 v5 reconciliation")
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
