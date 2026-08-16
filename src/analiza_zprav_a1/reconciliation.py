from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from .hashing import sha256_file
from .sqlite_snapshot import consistent_sqlite_snapshot

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


def _imessage_inventory(snapshot_path: Path) -> dict[str, Any]:
    """Inventory source-domain rows from the immutable SQLite snapshot.

    Message rows are authoritative source records. Conversation relations are
    deduplicated by `(message_id, chat_id)` because the A1 v2 contract emits one
    semantic source membership per pair; duplicate join rows are retained in the
    reconciliation report as explicit `duplicate` outcomes. Attachment relation
    multiplicity is preserved exactly because A1 emits each relation occurrence.
    """

    with _readonly_sqlite(snapshot_path) as conn:
        tables = _table_names(conn)
        if "message" not in tables:
            raise ValueError("Apple Messages reconciliation requires the message table")

        message_ids = {str(row[0]) for row in conn.execute("SELECT ROWID FROM message")}

        expected_conversation_pairs: set[tuple[str, str]] = set()
        duplicate_records: list[dict[str, Any]] = []
        unsupported_records: list[dict[str, Any]] = []
        raw_chat_link_rows = 0

        if "chat_message_join" in tables:
            rows = conn.execute(
                "SELECT ROWID, message_id, chat_id FROM chat_message_join ORDER BY ROWID"
            )
            seen_pairs: set[tuple[str, str]] = set()
            for row in rows:
                raw_chat_link_rows += 1
                rowid, raw_message_id, raw_chat_id = row
                if raw_message_id is None or raw_chat_id is None:
                    unsupported_records.append(
                        {
                            "record_type": "chat_message_join",
                            "source_identifier": str(rowid),
                            "outcome": "unsupported",
                            "reason": "missing message_id or chat_id",
                        }
                    )
                    continue
                message_id = str(raw_message_id)
                chat_id = str(raw_chat_id)
                if message_id not in message_ids:
                    unsupported_records.append(
                        {
                            "record_type": "chat_message_join",
                            "source_identifier": str(rowid),
                            "outcome": "unsupported",
                            "reason": "relation points to a missing message row",
                            "message_id": message_id,
                            "chat_id": chat_id,
                        }
                    )
                    continue
                pair = (message_id, chat_id)
                if pair in seen_pairs:
                    duplicate_records.append(
                        {
                            "record_type": "chat_message_join",
                            "source_identifier": str(rowid),
                            "outcome": "duplicate",
                            "reason": "duplicate message_id/chat_id source relation",
                            "message_id": message_id,
                            "chat_id": chat_id,
                        }
                    )
                    continue
                seen_pairs.add(pair)
                expected_conversation_pairs.add(pair)

        messages_with_conversation = {message_id for message_id, _ in expected_conversation_pairs}
        orphan_message_ids = message_ids - messages_with_conversation

        attachment_ids: set[str] = set()
        if "attachment" in tables:
            attachment_ids = {str(row[0]) for row in conn.execute("SELECT ROWID FROM attachment")}

        valid_attachment_pairs: Counter[tuple[str, str]] = Counter()
        all_join_attachment_ids: set[str] = set()
        raw_attachment_link_rows = 0
        if "message_attachment_join" in tables:
            rows = conn.execute(
                "SELECT ROWID, message_id, attachment_id FROM message_attachment_join ORDER BY ROWID"
            )
            for row in rows:
                raw_attachment_link_rows += 1
                rowid, raw_message_id, raw_attachment_id = row
                if raw_message_id is None or raw_attachment_id is None:
                    unsupported_records.append(
                        {
                            "record_type": "message_attachment_join",
                            "source_identifier": str(rowid),
                            "outcome": "unsupported",
                            "reason": "missing message_id or attachment_id",
                        }
                    )
                    continue
                message_id = str(raw_message_id)
                attachment_id = str(raw_attachment_id)
                all_join_attachment_ids.add(attachment_id)
                if message_id not in message_ids:
                    unsupported_records.append(
                        {
                            "record_type": "message_attachment_join",
                            "source_identifier": str(rowid),
                            "outcome": "unsupported",
                            "reason": "relation points to a missing message row",
                            "message_id": message_id,
                            "attachment_id": attachment_id,
                        }
                    )
                    continue
                if attachment_id not in attachment_ids:
                    unsupported_records.append(
                        {
                            "record_type": "message_attachment_join",
                            "source_identifier": str(rowid),
                            "outcome": "unsupported",
                            "reason": "relation points to a missing attachment row",
                            "message_id": message_id,
                            "attachment_id": attachment_id,
                        }
                    )
                    continue
                valid_attachment_pairs[(message_id, attachment_id)] += 1

        referenced_valid_attachment_ids = {
            attachment_id for _, attachment_id in valid_attachment_pairs
        }
        for attachment_id in sorted(
            attachment_ids - referenced_valid_attachment_ids,
            key=lambda value: (len(value), value),
        ):
            reason = (
                "attachment row is referenced only by an unsupported relation"
                if attachment_id in all_join_attachment_ids
                else "attachment row is not referenced by message_attachment_join"
            )
            unsupported_records.append(
                {
                    "record_type": "attachment",
                    "source_identifier": attachment_id,
                    "outcome": "unsupported",
                    "reason": reason,
                }
            )

        return {
            "message_ids": message_ids,
            "expected_conversation_pairs": expected_conversation_pairs,
            "orphan_message_ids": orphan_message_ids,
            "valid_attachment_pairs": valid_attachment_pairs,
            "unsupported_records": unsupported_records,
            "duplicate_records": duplicate_records,
            "counts": {
                "source_message_rows": len(message_ids),
                "source_chat_message_link_rows": raw_chat_link_rows,
                "source_unique_conversation_relations": len(expected_conversation_pairs),
                "source_orphan_messages": len(orphan_message_ids),
                "source_attachment_rows": len(attachment_ids),
                "source_message_attachment_link_rows": raw_attachment_link_rows,
                "source_valid_attachment_relations": sum(valid_attachment_pairs.values()),
                "source_duplicate_records": len(duplicate_records),
                "source_unsupported_records": len(unsupported_records),
            },
        }


def _reconcile_against_physical_source(
    bundle_dir: Path,
    source_path: Path,
    physical_source_path: Path,
) -> dict[str, Any]:
    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)

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
    # Legacy A1 error rows predate the explicit scope field. They are still
    # message-level outcomes when a source_message_id is present.
    message_errors.extend(
        record
        for record in errors
        if "scope" not in record and record.get("source_message_id") is not None
    )

    expected_source_type = source.get("type")
    expected_source_sha = source.get("sha256")
    actual_source_sha = sha256_file(physical_source_path)

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
        "source_sha256_matches": actual_source_sha == expected_source_sha,
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
    duplicate_records: list[dict[str, Any]] = []

    if expected_source_type == "imessage_chat_db":
        inventory = _imessage_inventory(physical_source_path)
        raw_counts.update(inventory["counts"])
        unsupported_records.extend(inventory["unsupported_records"])
        duplicate_records.extend(inventory["duplicate_records"])

        emitted_message_ids = {
            str(record["source_message_id"])
            for record in messages
            if record.get("source_message_id") is not None
        }
        errored_message_ids = {
            str(record["source_message_id"])
            for record in message_errors
            if record.get("source_message_id") is not None
        }
        actual_message_id_counter = Counter(
            str(record["source_message_id"])
            for record in messages + message_errors
            if record.get("source_message_id") is not None
        )
        expected_message_id_counter = Counter({message_id: 1 for message_id in inventory["message_ids"]})

        actual_conversation_pairs: set[tuple[str, str]] = set()
        actual_orphan_message_ids: set[str] = set()
        primary_conversation_consistent = True
        for record in messages:
            raw_message_id = record.get("source_message_id")
            if raw_message_id is None:
                continue
            message_id = str(raw_message_id)
            conversation_sources = record.get("conversation_sources") or []
            keys: list[str] = []
            for relation in conversation_sources:
                if not isinstance(relation, dict):
                    primary_conversation_consistent = False
                    continue
                source_key = relation.get("source_conversation_key")
                if source_key is not None:
                    keys.append(str(source_key))
                raw_chat_rowid = relation.get("raw_chat_rowid")
                if raw_chat_rowid is not None:
                    actual_conversation_pairs.add((message_id, str(raw_chat_rowid)))
                elif source_key == f"orphan:{message_id}":
                    actual_orphan_message_ids.add(message_id)
            if keys and str(record.get("conversation_source_id")) != keys[0]:
                primary_conversation_consistent = False

        accounted_conversation_pairs = actual_conversation_pairs | {
            pair
            for pair in inventory["expected_conversation_pairs"]
            if pair[0] in errored_message_ids
        }
        accounted_orphans = actual_orphan_message_ids | (
            inventory["orphan_message_ids"] & errored_message_ids
        )

        actual_attachment_pairs: Counter[tuple[str, str]] = Counter()
        for record in messages:
            raw_message_id = record.get("source_message_id")
            if raw_message_id is None:
                continue
            message_id = str(raw_message_id)
            for attachment in record.get("attachments") or []:
                if isinstance(attachment, dict) and attachment.get("source_attachment_id") is not None:
                    actual_attachment_pairs[(message_id, str(attachment["source_attachment_id"]))] += 1

        accounted_attachment_pairs = actual_attachment_pairs.copy()
        for pair, pair_count in inventory["valid_attachment_pairs"].items():
            if pair[0] in errored_message_ids and pair[0] not in emitted_message_ids:
                accounted_attachment_pairs[pair] = pair_count

        checks.update(
            {
                "imessage_snapshot_contract_declared": source.get("snapshot_method") == "sqlite_online_backup_v1"
                and source.get("snapshot_includes_committed_wal") is True,
                "source_message_rows_accounted": actual_message_id_counter == expected_message_id_counter,
                "source_conversation_relations_accounted": accounted_conversation_pairs
                == inventory["expected_conversation_pairs"],
                "source_orphan_messages_accounted": accounted_orphans == inventory["orphan_message_ids"],
                "primary_conversation_matches_first_source_relation": primary_conversation_consistent,
                "source_message_attachment_relations_accounted": accounted_attachment_pairs
                == inventory["valid_attachment_pairs"],
            }
        )

    failed_checks = [name for name, value in checks.items() if not value]
    return {
        "reconciliation_version": RECONCILIATION_VERSION,
        "status": "ok" if not failed_checks else "failed",
        "ok": not failed_checks,
        "source": {
            "type": expected_source_type,
            "name": source.get("name") or source_path.name,
            "sha256": expected_source_sha,
            "actual_sha256": actual_source_sha,
        },
        "bundle": {
            "messages_jsonl_records": len(messages),
            "errors_jsonl_records": len(errors),
            "message_error_records": len(message_errors),
        },
        "raw_counts": raw_counts,
        "unsupported_records": unsupported_records,
        "duplicate_records": duplicate_records,
        "checks": checks,
        "failed_checks": failed_checks,
        "parse_failures": parse_failures,
    }


def reconcile_bundle(
    bundle_dir: Path,
    source_path: Path,
    *,
    sqlite_snapshot_path: Path | None = None,
) -> dict[str, Any]:
    bundle_dir = bundle_dir.resolve()
    source_path = source_path.resolve()
    if not source_path.is_file():
        raise FileNotFoundError(source_path)

    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_type = (manifest.get("source") or {}).get("type")

    if source_type != "imessage_chat_db":
        return _reconcile_against_physical_source(bundle_dir, source_path, source_path)

    if sqlite_snapshot_path is not None:
        return _reconcile_against_physical_source(
            bundle_dir, source_path, sqlite_snapshot_path.resolve()
        )

    with consistent_sqlite_snapshot(source_path) as snapshot:
        return _reconcile_against_physical_source(bundle_dir, source_path, snapshot)


def write_reconciliation(report: dict[str, Any], path: Path) -> None:
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
