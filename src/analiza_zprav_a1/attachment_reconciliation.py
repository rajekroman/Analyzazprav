from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .conversation_row_reconciliation import reconcile_bundle as _base_reconcile_bundle
from .conversation_row_reconciliation import write_reconciliation
from .sqlite_snapshot import consistent_sqlite_snapshot

ATTACHMENT_RELATION_PAYLOAD_KEY = "__analyzazprav_a1_message_attachment_relation__"


def _readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _enabled(manifest: dict[str, Any]) -> bool:
    source = manifest.get("source") or {}
    parser = manifest.get("parser") or {}
    if source.get("type") != "imessage_chat_db" or parser.get("name") != "imessage-chatdb":
        return False
    version = str(parser.get("version") or "").split("+", 1)[0]
    try:
        parts = tuple(int(part) for part in version.split("."))
    except ValueError:
        return False
    return parts >= (0, 10, 0)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                values.append(value)
    return values


def _expected_occurrences(snapshot_path: Path) -> dict[str, list[dict[str, Any]]]:
    with _readonly(snapshot_path) as conn:
        tables = _tables(conn)
        if not {"message", "attachment", "message_attachment_join"}.issubset(tables):
            return {}

        message_ids = {int(row[0]) for row in conn.execute("SELECT ROWID FROM message")}
        attachment_ids = {int(row[0]) for row in conn.execute("SELECT ROWID FROM attachment")}
        rows = conn.execute(
            """
            SELECT maj.ROWID AS relation_rowid,
                   maj.message_id,
                   maj.attachment_id
            FROM message_attachment_join maj
            ORDER BY maj.message_id, maj.attachment_id, maj.ROWID
            """
        ).fetchall()

        result: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            raw_message_id = row["message_id"]
            raw_attachment_id = row["attachment_id"]
            if raw_message_id is None or raw_attachment_id is None:
                continue
            message_id = int(raw_message_id)
            attachment_id = int(raw_attachment_id)
            if message_id not in message_ids or attachment_id not in attachment_ids:
                continue
            bucket = result.setdefault(str(message_id), [])
            bucket.append(
                {
                    "source_relation_ordinal": len(bucket),
                    "raw_join_rowid": int(row["relation_rowid"]),
                    "raw_message_id": message_id,
                    "raw_attachment_id": attachment_id,
                    "resolution_status": "resolved",
                }
            )
        return result


def _validate(bundle_dir: Path, snapshot_path: Path) -> dict[str, Any]:
    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    outputs = manifest.get("outputs") or {}
    messages = _load_jsonl(bundle_dir / str(outputs.get("messages") or "messages.jsonl"))
    errors = _load_jsonl(bundle_dir / str(outputs.get("errors") or "errors.jsonl"))
    errored_message_ids = {
        str(item["source_message_id"])
        for item in errors
        if item.get("source_message_id") is not None
        and item.get("scope") in (None, "message")
    }
    expected_by_message = _expected_occurrences(snapshot_path)

    failures: list[dict[str, Any]] = []
    actual_count = 0
    emitted_message_ids: set[str] = set()
    for record in messages:
        raw_message_id = record.get("source_message_id")
        if raw_message_id is None:
            continue
        message_id = str(raw_message_id)
        emitted_message_ids.add(message_id)
        expected = expected_by_message.get(message_id, [])
        attachments = record.get("attachments") or []
        if not isinstance(attachments, list):
            failures.append(
                {
                    "source_message_id": message_id,
                    "reason": "attachments is not an array",
                    "expected_occurrences": expected,
                    "actual_occurrences": None,
                }
            )
            continue

        actual: list[dict[str, Any] | None] = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                actual.append(None)
                continue
            raw_payload = attachment.get("raw_payload")
            relation = (
                raw_payload.get(ATTACHMENT_RELATION_PAYLOAD_KEY)
                if isinstance(raw_payload, dict)
                else None
            )
            actual.append(relation if isinstance(relation, dict) else None)
        actual_count += len(actual)

        if actual != expected:
            failures.append(
                {
                    "source_message_id": message_id,
                    "reason": "attachment relation provenance differs from source snapshot",
                    "expected_occurrences": expected,
                    "actual_occurrences": actual,
                }
            )
            continue

        for attachment, relation in zip(attachments, expected):
            if not isinstance(attachment, dict):
                continue
            if str(attachment.get("source_attachment_id") or "") != str(
                relation["raw_attachment_id"]
            ):
                failures.append(
                    {
                        "source_message_id": message_id,
                        "reason": "source_attachment_id disagrees with attachment relation target",
                        "expected": relation,
                        "actual_source_attachment_id": attachment.get("source_attachment_id"),
                    }
                )

    for message_id, expected in expected_by_message.items():
        if message_id in errored_message_ids or message_id in emitted_message_ids:
            continue
        failures.append(
            {
                "source_message_id": message_id,
                "reason": "source message with valid attachment relations is missing from staging",
                "expected_occurrences": expected,
                "actual_occurrences": None,
            }
        )

    expected_count = sum(len(items) for items in expected_by_message.values())
    return {
        "ok": not failures,
        "failures": failures,
        "counts": {
            "source_valid_attachment_relation_rows": expected_count,
            "source_attachment_relation_provenance_occurrences": actual_count,
        },
    }


def _augment(base: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    check_name = "source_attachment_relation_provenance_matches_snapshot"
    checks = base.setdefault("checks", {})
    if isinstance(checks, dict):
        checks[check_name] = bool(validation["ok"])

    raw_counts = base.setdefault("raw_counts", {})
    if isinstance(raw_counts, dict):
        raw_counts.update(validation["counts"])

    base["attachment_relation_provenance"] = {
        "ok": bool(validation["ok"]),
        "failure_count": len(validation["failures"]),
        "failures": validation["failures"],
    }
    if not validation["ok"]:
        failed = base.setdefault("failed_checks", [])
        if isinstance(failed, list) and check_name not in failed:
            failed.append(check_name)
        base["ok"] = False
        base["status"] = "failed"
    return base


def reconcile_bundle(
    bundle_dir: Path,
    source_path: Path,
    *,
    sqlite_snapshot_path: Path | None = None,
) -> dict[str, Any]:
    """Run all existing A1 checks plus exact valid attachment-relation provenance."""

    bundle_dir = bundle_dir.resolve()
    source_path = source_path.resolve()
    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    if not _enabled(manifest):
        return _base_reconcile_bundle(
            bundle_dir,
            source_path,
            sqlite_snapshot_path=sqlite_snapshot_path,
        )

    if sqlite_snapshot_path is not None:
        snapshot = sqlite_snapshot_path.resolve()
        base = _base_reconcile_bundle(
            bundle_dir,
            source_path,
            sqlite_snapshot_path=snapshot,
        )
        return _augment(base, _validate(bundle_dir, snapshot))

    with consistent_sqlite_snapshot(source_path) as snapshot:
        base = _base_reconcile_bundle(
            bundle_dir,
            source_path,
            sqlite_snapshot_path=snapshot,
        )
        return _augment(base, _validate(bundle_dir, snapshot))
