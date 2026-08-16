from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .reconciliation import reconcile_bundle as _base_reconcile_bundle
from .sqlite_snapshot import consistent_sqlite_snapshot


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


def _relation_provenance_enabled(manifest: dict[str, Any]) -> bool:
    source = manifest.get("source") or {}
    parser = manifest.get("parser") or {}
    if source.get("type") != "imessage_chat_db" or parser.get("name") != "imessage-chatdb":
        return False
    version = str(parser.get("version") or "").split("+", 1)[0]
    try:
        parts = tuple(int(part) for part in version.split("."))
    except ValueError:
        return False
    return parts >= (0, 8, 0)


def _participant_relations(
    conn: sqlite3.Connection,
    chat_id: int,
    tables: set[str],
) -> list[dict[str, Any]]:
    if "chat_handle_join" not in tables:
        return []

    if "handle" in tables:
        rows = conn.execute(
            """
            SELECT chj.handle_id AS raw_handle_id,
                   h.ROWID AS resolved_handle_rowid,
                   h.id AS handle_value
            FROM chat_handle_join chj
            LEFT JOIN handle h ON h.ROWID=chj.handle_id
            WHERE chj.chat_id=?
            ORDER BY CASE WHEN chj.handle_id IS NULL THEN 0 ELSE 1 END,
                     chj.handle_id,
                     h.ROWID
            """,
            (chat_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT chj.handle_id AS raw_handle_id,
                   NULL AS resolved_handle_rowid,
                   NULL AS handle_value
            FROM chat_handle_join chj
            WHERE chj.chat_id=?
            ORDER BY CASE WHEN chj.handle_id IS NULL THEN 0 ELSE 1 END,
                     chj.handle_id
            """,
            (chat_id,),
        ).fetchall()

    result: list[dict[str, Any]] = []
    for ordinal, row in enumerate(rows):
        raw_handle_id = row["raw_handle_id"]
        resolved_rowid = row["resolved_handle_rowid"]
        handle_value = row["handle_value"]
        if raw_handle_id is None:
            status = "missing_handle_id"
        elif "handle" not in tables:
            status = "handle_table_missing"
        elif resolved_rowid is None:
            status = "missing_handle_row"
        elif handle_value is None:
            status = "handle_value_null"
        else:
            status = "resolved"

        item: dict[str, Any] = {
            "source_relation_ordinal": ordinal,
            "raw_chat_rowid": chat_id,
            "raw_handle_id": raw_handle_id,
            "resolution_status": status,
        }
        if resolved_rowid is not None:
            item["resolved_handle_rowid"] = int(resolved_rowid)
        if handle_value is not None:
            item["handle"] = str(handle_value)
        result.append(item)
    return result


def _expected_chat_provenance(
    conn: sqlite3.Connection,
    chat_id: int,
    tables: set[str],
) -> dict[str, Any]:
    if "chat" not in tables:
        chat_status = "chat_table_missing"
    else:
        row = conn.execute("SELECT 1 FROM chat WHERE ROWID=?", (chat_id,)).fetchone()
        chat_status = "resolved" if row is not None else "missing_chat_row"
    return {
        "chat": {
            "raw_chat_rowid": chat_id,
            "resolution_status": chat_status,
        },
        "participant_relations": _participant_relations(conn, chat_id, tables),
    }


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                result.append(value)
    return result


def _validate_relation_provenance(
    bundle_dir: Path,
    snapshot_path: Path,
) -> dict[str, Any]:
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

    failures: list[dict[str, Any]] = []
    actual_relations = 0
    expected_participant_relation_rows = 0
    unresolved_chat_relations = 0
    unresolved_participant_relations = 0

    with _readonly(snapshot_path) as conn:
        tables = _tables(conn)
        message_ids = {str(row[0]) for row in conn.execute("SELECT ROWID FROM message")}
        expected_pairs: set[tuple[str, int]] = set()
        if "chat_message_join" in tables:
            for row in conn.execute(
                "SELECT message_id, chat_id FROM chat_message_join ORDER BY message_id, chat_id"
            ):
                if row[0] is None or row[1] is None:
                    continue
                message_id = str(row[0])
                if message_id not in message_ids:
                    continue
                expected_pairs.add((message_id, int(row[1])))

        expected_by_chat = {
            chat_id: _expected_chat_provenance(conn, chat_id, tables)
            for _, chat_id in expected_pairs
        }
        expected_participant_relation_rows = sum(
            len(value["participant_relations"])
            for value in expected_by_chat.values()
        )
        unresolved_chat_relations = sum(
            value["chat"]["resolution_status"] != "resolved"
            for value in expected_by_chat.values()
        )
        unresolved_participant_relations = sum(
            item["resolution_status"] != "resolved"
            for value in expected_by_chat.values()
            for item in value["participant_relations"]
        )

        actual_pairs: set[tuple[str, int]] = set()
        for record in messages:
            raw_message_id = record.get("source_message_id")
            if raw_message_id is None:
                continue
            message_id = str(raw_message_id)
            for relation in record.get("conversation_sources") or []:
                if not isinstance(relation, dict):
                    continue
                raw_chat_rowid = relation.get("raw_chat_rowid")
                if raw_chat_rowid is None:
                    continue
                chat_id = int(raw_chat_rowid)
                actual_relations += 1
                actual_pairs.add((message_id, chat_id))
                expected = expected_by_chat.get(chat_id)
                metadata = relation.get("metadata")
                actual = (
                    metadata.get("_a1_source_relation")
                    if isinstance(metadata, dict)
                    else None
                )
                if expected is None or actual != expected:
                    failures.append(
                        {
                            "source_message_id": message_id,
                            "raw_chat_rowid": chat_id,
                            "expected": expected,
                            "actual": actual,
                        }
                    )

        required_pairs = {
            pair for pair in expected_pairs if pair[0] not in errored_message_ids
        }
        missing_pairs = sorted(required_pairs - actual_pairs)
        for message_id, chat_id in missing_pairs:
            failures.append(
                {
                    "source_message_id": message_id,
                    "raw_chat_rowid": chat_id,
                    "expected": expected_by_chat.get(chat_id),
                    "actual": None,
                    "reason": "expected source relation is missing from staging",
                }
            )

    return {
        "ok": not failures,
        "failures": failures,
        "counts": {
            "source_relation_provenance_relations": actual_relations,
            "source_relevant_chat_handle_link_rows": expected_participant_relation_rows,
            "source_unresolved_chat_references": unresolved_chat_relations,
            "source_unresolved_participant_relations": unresolved_participant_relations,
        },
    }


def _augment_base_report(
    base: dict[str, Any],
    relation: dict[str, Any],
) -> dict[str, Any]:
    raw_counts = base.setdefault("raw_counts", {})
    if isinstance(raw_counts, dict):
        raw_counts.update(relation["counts"])

    checks = base.setdefault("checks", {})
    if isinstance(checks, dict):
        checks["source_relation_provenance_matches_snapshot"] = bool(relation["ok"])

    base["relation_provenance"] = {
        "ok": bool(relation["ok"]),
        "failure_count": len(relation["failures"]),
        "failures": relation["failures"],
    }

    if not relation["ok"]:
        failed = base.setdefault("failed_checks", [])
        if isinstance(failed, list) and "source_relation_provenance_matches_snapshot" not in failed:
            failed.append("source_relation_provenance_matches_snapshot")
        base["ok"] = False
        base["status"] = "failed"
    return base


def reconcile_bundle(
    bundle_dir: Path,
    source_path: Path,
    *,
    sqlite_snapshot_path: Path | None = None,
) -> dict[str, Any]:
    """Run authoritative A1 reconciliation plus iMessage relation provenance checks."""

    bundle_dir = bundle_dir.resolve()
    source_path = source_path.resolve()
    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    if not _relation_provenance_enabled(manifest):
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
        return _augment_base_report(
            base,
            _validate_relation_provenance(bundle_dir, snapshot),
        )

    with consistent_sqlite_snapshot(source_path) as snapshot:
        base = _base_reconcile_bundle(
            bundle_dir,
            source_path,
            sqlite_snapshot_path=snapshot,
        )
        return _augment_base_report(
            base,
            _validate_relation_provenance(bundle_dir, snapshot),
        )


def write_reconciliation(report: dict[str, Any], path: Path) -> None:
    from .reconciliation import write_reconciliation as _write

    _write(report, path)
