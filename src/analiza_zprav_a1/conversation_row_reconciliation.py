from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .reconciliation import reconcile_bundle as _base_reconcile_bundle
from .relation_reconciliation import reconcile_bundle as _legacy_reconcile_bundle
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


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    escaped = table.replace("'", "''")
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info('{escaped}')")}


def _parser_version(manifest: dict[str, Any]) -> tuple[int, ...] | None:
    source = manifest.get("source") or {}
    parser = manifest.get("parser") or {}
    if source.get("type") != "imessage_chat_db" or parser.get("name") != "imessage-chatdb":
        return None
    version = str(parser.get("version") or "").split("+", 1)[0]
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return None


def _enabled(manifest: dict[str, Any]) -> bool:
    version = _parser_version(manifest)
    return version is not None and version >= (0, 11, 0)


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
            SELECT chj.ROWID AS relation_rowid,
                   chj.handle_id AS raw_handle_id,
                   h.ROWID AS resolved_handle_rowid,
                   h.id AS handle_value
            FROM chat_handle_join chj
            LEFT JOIN handle h ON h.ROWID=chj.handle_id
            WHERE chj.chat_id=?
            ORDER BY CASE WHEN chj.handle_id IS NULL THEN 0 ELSE 1 END,
                     chj.handle_id,
                     chj.ROWID
            """,
            (chat_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT chj.ROWID AS relation_rowid,
                   chj.handle_id AS raw_handle_id,
                   NULL AS resolved_handle_rowid,
                   NULL AS handle_value
            FROM chat_handle_join chj
            WHERE chj.chat_id=?
            ORDER BY CASE WHEN chj.handle_id IS NULL THEN 0 ELSE 1 END,
                     chj.handle_id,
                     chj.ROWID
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
            "raw_join_rowid": int(row["relation_rowid"]),
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


def _expected_sender_provenance(
    conn: sqlite3.Connection,
    message_id: str,
    message_columns: set[str],
    tables: set[str],
) -> dict[str, Any]:
    if "handle_id" not in message_columns:
        return {
            "raw_handle_id": None,
            "resolution_status": "handle_id_column_missing",
        }

    row = conn.execute(
        "SELECT handle_id FROM message WHERE ROWID=?",
        (int(message_id),),
    ).fetchone()
    raw_handle_id = None if row is None else row[0]
    if raw_handle_id is None:
        return {
            "raw_handle_id": None,
            "resolution_status": "missing_handle_id",
        }
    if "handle" not in tables:
        return {
            "raw_handle_id": raw_handle_id,
            "resolution_status": "handle_table_missing",
        }

    handle_row = conn.execute(
        "SELECT ROWID, id FROM handle WHERE ROWID=?",
        (raw_handle_id,),
    ).fetchone()
    if handle_row is None:
        return {
            "raw_handle_id": raw_handle_id,
            "resolution_status": "missing_handle_row",
        }

    result: dict[str, Any] = {
        "raw_handle_id": raw_handle_id,
        "resolved_handle_rowid": int(handle_row[0]),
        "resolution_status": (
            "handle_value_null" if handle_row[1] is None else "resolved"
        ),
    }
    if handle_row[1] is not None:
        result["handle"] = str(handle_row[1])
    return result


def _valid_conversation_rows(
    conn: sqlite3.Connection,
    message_ids: set[str],
    tables: set[str],
) -> dict[tuple[str, int], int]:
    """Return the first source join ROWID for each valid message/chat pair."""

    if "chat_message_join" not in tables:
        return {}
    result: dict[tuple[str, int], int] = {}
    rows = conn.execute(
        "SELECT ROWID, message_id, chat_id FROM chat_message_join ORDER BY ROWID"
    )
    for row in rows:
        raw_message_id = row[1]
        raw_chat_id = row[2]
        if raw_message_id is None or raw_chat_id is None:
            continue
        message_id = str(raw_message_id)
        if message_id not in message_ids:
            continue
        pair = (message_id, int(raw_chat_id))
        result.setdefault(pair, int(row[0]))
    return result


def _expected_chat_provenance(
    conn: sqlite3.Connection,
    chat_id: int,
    join_rowid: int,
    tables: set[str],
) -> dict[str, Any]:
    if "chat" not in tables:
        chat_status = "chat_table_missing"
    else:
        row = conn.execute("SELECT 1 FROM chat WHERE ROWID=?", (chat_id,)).fetchone()
        chat_status = "resolved" if row is not None else "missing_chat_row"
    return {
        "chat": {
            "raw_join_rowid": join_rowid,
            "raw_chat_rowid": chat_id,
            "resolution_status": chat_status,
        },
        "participant_relations": _participant_relations(conn, chat_id, tables),
    }


def _chat_handle_accounting(
    conn: sqlite3.Connection,
    relevant_chat_ids: set[int],
    tables: set[str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if "chat_handle_join" not in tables:
        return [], {
            "source_chat_handle_link_rows": 0,
            "source_relevant_chat_handle_link_rows": 0,
            "source_unreferenced_chat_handle_link_rows": 0,
        }

    unsupported: list[dict[str, Any]] = []
    total = relevant = unreferenced = 0
    for row in conn.execute(
        "SELECT ROWID, chat_id, handle_id FROM chat_handle_join ORDER BY ROWID"
    ):
        total += 1
        rowid = str(row[0])
        raw_chat_id = row[1]
        raw_handle_id = row[2]
        if raw_chat_id is not None and int(raw_chat_id) in relevant_chat_ids:
            relevant += 1
            continue

        unreferenced += 1
        if raw_chat_id is None:
            reason = "chat participant relation has no chat_id"
        else:
            reason = "chat participant relation is outside imported message conversation domain"
        item: dict[str, Any] = {
            "record_type": "chat_handle_join",
            "source_identifier": rowid,
            "outcome": "unsupported",
            "reason": reason,
            "chat_id": None if raw_chat_id is None else str(raw_chat_id),
            "handle_id": None if raw_handle_id is None else str(raw_handle_id),
        }
        unsupported.append(item)

    return unsupported, {
        "source_chat_handle_link_rows": total,
        "source_relevant_chat_handle_link_rows": relevant,
        "source_unreferenced_chat_handle_link_rows": unreferenced,
    }


def _validate(
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

    relation_failures: list[dict[str, Any]] = []
    sender_failures: list[dict[str, Any]] = []
    actual_relations = 0
    unresolved_chat_relations = 0
    unresolved_participant_relations = 0
    sender_handle_ids_present = 0
    sender_handle_ids_resolved = 0
    sender_handle_ids_unresolved = 0
    sender_handle_ids_null = 0
    sender_handle_id_column_missing = 0

    with _readonly(snapshot_path) as conn:
        tables = _tables(conn)
        message_columns = _columns(conn, "message")
        message_ids = {str(row[0]) for row in conn.execute("SELECT ROWID FROM message")}
        pair_rowids = _valid_conversation_rows(conn, message_ids, tables)
        relevant_chat_ids = {chat_id for _, chat_id in pair_rowids}
        expected_by_pair = {
            pair: _expected_chat_provenance(conn, pair[1], join_rowid, tables)
            for pair, join_rowid in pair_rowids.items()
        }

        unresolved_chat_relations = sum(
            value["chat"]["resolution_status"] != "resolved"
            for value in expected_by_pair.values()
        )
        # Participant rows are chat-level source occurrences. Count each source
        # row once per relevant chat, even when several messages share that chat.
        participant_by_chat = {
            chat_id: _participant_relations(conn, chat_id, tables)
            for chat_id in relevant_chat_ids
        }
        unresolved_participant_relations = sum(
            item["resolution_status"] != "resolved"
            for items in participant_by_chat.values()
            for item in items
        )

        auxiliary_unsupported, chat_handle_counts = _chat_handle_accounting(
            conn, relevant_chat_ids, tables
        )

        expected_sender_by_message = {
            message_id: _expected_sender_provenance(
                conn, message_id, message_columns, tables
            )
            for message_id in message_ids
        }
        for expected in expected_sender_by_message.values():
            status = expected["resolution_status"]
            raw_handle_id = expected.get("raw_handle_id")
            if status == "handle_id_column_missing":
                sender_handle_id_column_missing += 1
            elif raw_handle_id is None:
                sender_handle_ids_null += 1
            else:
                sender_handle_ids_present += 1
                if status == "resolved":
                    sender_handle_ids_resolved += 1
                else:
                    sender_handle_ids_unresolved += 1

        actual_pairs: set[tuple[str, int]] = set()
        for record in messages:
            raw_message_id = record.get("source_message_id")
            if raw_message_id is None:
                continue
            message_id = str(raw_message_id)

            expected_sender = expected_sender_by_message.get(message_id)
            metadata = record.get("metadata")
            actual_sender = (
                metadata.get("_a1_sender_relation")
                if isinstance(metadata, dict)
                else None
            )
            expected_sender_handle = (
                expected_sender.get("handle")
                if isinstance(expected_sender, dict)
                and expected_sender.get("resolution_status") == "resolved"
                else None
            )
            if (
                expected_sender is None
                or actual_sender != expected_sender
                or record.get("sender_handle") != expected_sender_handle
            ):
                sender_failures.append(
                    {
                        "source_message_id": message_id,
                        "expected": expected_sender,
                        "actual": actual_sender,
                        "expected_sender_handle": expected_sender_handle,
                        "actual_sender_handle": record.get("sender_handle"),
                    }
                )

            for relation in record.get("conversation_sources") or []:
                if not isinstance(relation, dict):
                    continue
                raw_chat_rowid = relation.get("raw_chat_rowid")
                if raw_chat_rowid is None:
                    continue
                chat_id = int(raw_chat_rowid)
                pair = (message_id, chat_id)
                actual_relations += 1
                actual_pairs.add(pair)
                expected = expected_by_pair.get(pair)
                relation_metadata = relation.get("metadata")
                actual = (
                    relation_metadata.get("_a1_source_relation")
                    if isinstance(relation_metadata, dict)
                    else None
                )
                if expected is None or actual != expected:
                    relation_failures.append(
                        {
                            "source_message_id": message_id,
                            "raw_chat_rowid": chat_id,
                            "expected": expected,
                            "actual": actual,
                        }
                    )

        required_pairs = {
            pair for pair in pair_rowids if pair[0] not in errored_message_ids
        }
        for message_id, chat_id in sorted(required_pairs - actual_pairs):
            relation_failures.append(
                {
                    "source_message_id": message_id,
                    "raw_chat_rowid": chat_id,
                    "expected": expected_by_pair.get((message_id, chat_id)),
                    "actual": None,
                    "reason": "expected source relation is missing from staging",
                }
            )

        counts = {
            "source_relation_provenance_relations": actual_relations,
            "source_unresolved_chat_references": unresolved_chat_relations,
            "source_unresolved_participant_relations": unresolved_participant_relations,
            "source_sender_handle_ids_present": sender_handle_ids_present,
            "source_sender_handle_ids_resolved": sender_handle_ids_resolved,
            "source_sender_handle_ids_unresolved": sender_handle_ids_unresolved,
            "source_sender_handle_ids_null": sender_handle_ids_null,
            "source_sender_handle_id_column_missing": sender_handle_id_column_missing,
            **chat_handle_counts,
        }
        chat_handle_accounted = (
            chat_handle_counts["source_chat_handle_link_rows"]
            == chat_handle_counts["source_relevant_chat_handle_link_rows"]
            + chat_handle_counts["source_unreferenced_chat_handle_link_rows"]
        )

    return {
        "relation_ok": not relation_failures,
        "relation_failures": relation_failures,
        "sender_ok": not sender_failures,
        "sender_failures": sender_failures,
        "chat_handle_accounted": chat_handle_accounted,
        "auxiliary_unsupported_records": auxiliary_unsupported,
        "counts": counts,
    }


def _mark_failed(base: dict[str, Any], check_name: str) -> None:
    failed = base.setdefault("failed_checks", [])
    if isinstance(failed, list) and check_name not in failed:
        failed.append(check_name)
    base["ok"] = False
    base["status"] = "failed"


def _augment(base: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    unsupported = base.setdefault("unsupported_records", [])
    if isinstance(unsupported, list):
        unsupported.extend(validation["auxiliary_unsupported_records"])

    raw_counts = base.setdefault("raw_counts", {})
    if isinstance(raw_counts, dict):
        raw_counts.update(validation["counts"])
        if isinstance(unsupported, list):
            raw_counts["source_unsupported_records"] = len(unsupported)

    checks = base.setdefault("checks", {})
    relation_check = "source_relation_provenance_matches_snapshot"
    sender_check = "source_sender_provenance_matches_snapshot"
    accounting_check = "source_chat_handle_rows_accounted"
    if isinstance(checks, dict):
        checks[relation_check] = bool(validation["relation_ok"])
        checks[sender_check] = bool(validation["sender_ok"])
        checks[accounting_check] = bool(validation["chat_handle_accounted"])

    base["relation_provenance"] = {
        "ok": bool(validation["relation_ok"]),
        "failure_count": len(validation["relation_failures"]),
        "failures": validation["relation_failures"],
    }
    base["sender_provenance"] = {
        "ok": bool(validation["sender_ok"]),
        "failure_count": len(validation["sender_failures"]),
        "failures": validation["sender_failures"],
    }
    base["auxiliary_relation_outcomes"] = {
        "unsupported_chat_handle_rows": len(validation["auxiliary_unsupported_records"]),
    }

    if not validation["relation_ok"]:
        _mark_failed(base, relation_check)
    if not validation["sender_ok"]:
        _mark_failed(base, sender_check)
    if not validation["chat_handle_accounted"]:
        _mark_failed(base, accounting_check)
    return base


def reconcile_bundle(
    bundle_dir: Path,
    source_path: Path,
    *,
    sqlite_snapshot_path: Path | None = None,
) -> dict[str, Any]:
    """Versioned exact conversation/participant relation reconciliation."""

    bundle_dir = bundle_dir.resolve()
    source_path = source_path.resolve()
    manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    if not _enabled(manifest):
        return _legacy_reconcile_bundle(
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


def write_reconciliation(report: dict[str, Any], path: Path) -> None:
    from .reconciliation import write_reconciliation as _write

    _write(report, path)
