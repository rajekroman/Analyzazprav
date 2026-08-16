from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from .hashing import sha256_file

RECONCILIATION_VERSION = "1"


def _output_path(bundle_dir: Path, value: str) -> Path:
    root = bundle_dir.resolve()
    candidate = (bundle_dir / value).resolve()
    if candidate.parent != root:
        raise ValueError(f"A1 output must remain directly inside the staging directory: {value}")
    return candidate


def _read_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    failures: list[str] = []
    if not path.is_file():
        return records, [f"missing file: {path.name}"]
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                failures.append(f"{path.name}:{line_number}: {exc.msg}")
                continue
            if not isinstance(value, dict):
                failures.append(f"{path.name}:{line_number}: record is not an object")
                continue
            records.append(value)
    return records, failures


def _readonly_sqlite(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _imessage_inventory(path: Path) -> dict[str, Any]:
    with _readonly_sqlite(path) as conn:
        tables = _table_names(conn)
        if "message" not in tables:
            raise ValueError("Apple Messages reconciliation requires the message table")

        message_ids = {
            str(row[0]) for row in conn.execute("SELECT ROWID FROM message")
        }

        membership_pairs: Counter[tuple[str, str]] = Counter()
        if "chat_message_join" in tables:
            for row in conn.execute("SELECT message_id, chat_id FROM chat_message_join"):
                membership_pairs[(str(row[0]), str(row[1]))] += 1

        joined_message_ids = {message_id for message_id, _ in membership_pairs}
        orphan_message_ids = message_ids - joined_message_ids
        parser_pairs = membership_pairs.copy()
        for message_id in orphan_message_ids:
            parser_pairs[(message_id, f"orphan:{message_id}")] += 1

        attachment_ids: set[str] = set()
        if "attachment" in tables:
            attachment_ids = {
                str(row[0]) for row in conn.execute("SELECT ROWID FROM attachment")
            }

        message_attachment_pairs: set[tuple[str, str]] = set()
        if "message_attachment_join" in tables:
            message_attachment_pairs = {
                (str(row[0]), str(row[1]))
                for row in conn.execute(
                    "SELECT message_id, attachment_id FROM message_attachment_join"
                )
            }

        referenced_attachment_ids = {
            attachment_id for _, attachment_id in message_attachment_pairs
        }
        unreferenced_attachment_ids = sorted(
            attachment_ids - referenced_attachment_ids,
            key=lambda value: (len(value), value),
        )

        return {
            "message_ids": message_ids,
            "membership_pairs": membership_pairs,
            "orphan_message_ids": orphan_message_ids,
            "parser_pairs": parser_pairs,
            "attachment_ids": attachment_ids,
            "message_attachment_pairs": message_attachment_pairs,
            "unreferenced_attachment_ids": unreferenced_attachment_ids,
            "counts": {
                "source_message_rows": len(message_ids),
                "source_chat_message_links": sum(membership_pairs.values()),
                "source_orphan_messages": len(orphan_message_ids),
                "expected_parser_records": sum(parser_pairs.values()),
                "source_attachment_rows": len(attachment_ids),
                "source_message_attachment_links": len(message_attachment_pairs),
                "source_unreferenced_attachments": len(unreferenced_attachment_ids),
            },
        }


def reconcile_bundle(bundle_dir: Path, source_path: Path) -> dict[str, Any]:
    bundle_dir = bundle_dir.resolve()
    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    outputs = manifest.get("outputs") or {}
    counts = manifest.get("counts") or {}
    source = manifest.get("source") or {}

    messages_path = _output_path(bundle_dir, str(outputs.get("messages", "messages.jsonl")))
    errors_path = _output_path(bundle_dir, str(outputs.get("errors", "errors.jsonl")))
    messages, message_parse_failures = _read_jsonl(messages_path)
    errors, error_parse_failures = _read_jsonl(errors_path)
    parse_failures = message_parse_failures + error_parse_failures

    message_errors = [
        record
        for record in errors
        if record.get("scope", "message") == "message"
        and record.get("source_message_id") is not None
    ]

    expected_source_type = source.get("type")
    expected_source_sha = source.get("sha256")
    source_hash = sha256_file(source_path)

    record_keys = [record.get("source_record_key") for record in messages]
    identity_matches = all(
        record.get("source_type") == expected_source_type
        and record.get("source_sha256") == expected_source_sha
        for record in messages
    )

    manifest_message_errors = counts.get("message_errors")
    if manifest_message_errors is None:
        manifest_message_errors = counts.get("errors", 0)

    checks: dict[str, bool] = {
        "source_sha256_matches": source_hash == expected_source_sha,
        "jsonl_files_parse": not parse_failures,
        "messages_emitted_matches_file": int(counts.get("messages_emitted", len(messages))) == len(messages),
        "errors_match_file": int(counts.get("errors", len(errors))) == len(errors),
        "message_errors_match_file": int(manifest_message_errors) == len(message_errors),
        "messages_seen_accounted": int(counts.get("messages_seen", 0)) == len(messages) + len(message_errors),
        "message_source_identity_matches_manifest": identity_matches,
        "source_record_keys_present": all(isinstance(key, str) and bool(key) for key in record_keys),
        "source_record_keys_unique": len(record_keys) == len(set(record_keys)),
    }

    raw_counts: dict[str, Any] = {}
    unsupported_records: list[dict[str, Any]] = []

    if expected_source_type == "imessage_chat_db":
        inventory = _imessage_inventory(source_path)
        raw_counts.update(inventory["counts"])

        outcome_records = messages + message_errors
        actual_pairs: Counter[tuple[str, str]] = Counter()
        actual_message_ids: set[str] = set()
        for record in outcome_records:
            source_message_id = record.get("source_message_id")
            conversation_source_id = record.get("conversation_source_id")
            if source_message_id is None or conversation_source_id is None:
                continue
            source_message_id = str(source_message_id)
            actual_message_ids.add(source_message_id)
            actual_pairs[(source_message_id, str(conversation_source_id))] += 1

        errored_message_ids = {
            str(record["source_message_id"])
            for record in message_errors
            if record.get("source_message_id") is not None
        }
        actual_attachment_pairs: set[tuple[str, str]] = set()
        for record in messages:
            source_message_id = record.get("source_message_id")
            if source_message_id is None:
                continue
            for attachment in record.get("attachments") or []:
                if isinstance(attachment, dict) and attachment.get("source_attachment_id") is not None:
                    actual_attachment_pairs.add(
                        (str(source_message_id), str(attachment["source_attachment_id"]))
                    )

        accounted_attachment_pairs = actual_attachment_pairs | {
            pair
            for pair in inventory["message_attachment_pairs"]
            if pair[0] in errored_message_ids
        }

        checks.update(
            {
                "source_message_rows_accounted": actual_message_ids == inventory["message_ids"],
                "source_parser_records_accounted": actual_pairs == inventory["parser_pairs"],
                "source_message_attachment_links_accounted": accounted_attachment_pairs
                == inventory["message_attachment_pairs"],
            }
        )

        wal_path = Path(str(source_path) + "-wal")
        wal_bytes = wal_path.stat().st_size if wal_path.is_file() else 0
        raw_counts["source_wal_bytes"] = wal_bytes
        checks["sqlite_wal_sidecar_clear"] = wal_bytes == 0

        unsupported_records.extend(
            {
                "record_type": "attachment",
                "source_identifier": attachment_id,
                "outcome": "unsupported",
                "reason": "attachment row is not referenced by message_attachment_join",
            }
            for attachment_id in inventory["unreferenced_attachment_ids"]
        )

    failed_checks = [name for name, value in checks.items() if not value]
    return {
        "reconciliation_version": RECONCILIATION_VERSION,
        "status": "ok" if not failed_checks else "failed",
        "ok": not failed_checks,
        "source": {
            "type": expected_source_type,
            "name": source.get("name"),
            "sha256": expected_source_sha,
            "actual_sha256": source_hash,
        },
        "bundle": {
            "messages_jsonl_records": len(messages),
            "errors_jsonl_records": len(errors),
            "message_error_records": len(message_errors),
        },
        "raw_counts": raw_counts,
        "unsupported_records": unsupported_records,
        "checks": checks,
        "failed_checks": failed_checks,
        "parse_failures": parse_failures,
    }


def write_reconciliation(report: dict[str, Any], path: Path) -> None:
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
