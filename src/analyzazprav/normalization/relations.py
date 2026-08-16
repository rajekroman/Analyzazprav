from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping

from .database import CanonicalDatabase


def source_relation_key(
    *,
    source_record_key: str,
    relation_type: str,
    target_source_guid: str | None,
    target_service: str | None,
    position: int = 0,
) -> str:
    """Return a stable identity for one explicit source relation occurrence."""

    payload = {
        "source_record_key": source_record_key,
        "relation_type": relation_type,
        "target_source_guid": target_source_guid,
        "target_service": target_service,
        "position": position,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "rel:v1:" + sha256(raw).hexdigest()


def add_source_relation(
    db: CanonicalDatabase,
    *,
    message_source_id: int,
    import_run_id: int,
    source_record_key: str,
    relation_type: str,
    target_source_guid: str | None,
    target_service: str | None,
    position: int = 0,
    metadata: Mapping[str, Any] | None = None,
) -> int:
    """Persist one source relation fact before canonical target resolution."""

    relation_type = relation_type.strip()
    if not relation_type:
        raise ValueError("source relation type must not be empty")
    source_record_key = source_record_key.strip()
    if not source_record_key:
        raise ValueError("source relation requires source_record_key")

    source_row = db.conn.execute(
        "SELECT import_run_id FROM message_source WHERE id=?",
        (message_source_id,),
    ).fetchone()
    if source_row is None:
        raise ValueError(f"Unknown message_source_id: {message_source_id}")
    if int(source_row["import_run_id"]) != int(import_run_id):
        raise ValueError("source relation import_run_id disagrees with message_source")

    key = source_relation_key(
        source_record_key=source_record_key,
        relation_type=relation_type,
        target_source_guid=target_source_guid,
        target_service=target_service,
        position=position,
    )
    existing = db.conn.execute(
        """SELECT id FROM message_relation_source
           WHERE import_run_id=? AND source_relation_key=?""",
        (import_run_id, key),
    ).fetchone()
    if existing is not None:
        return int(existing["id"])

    payload = json.dumps(
        metadata or {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with db.conn:
        cur = db.conn.execute(
            """INSERT INTO message_relation_source(
                   message_source_id, import_run_id, source_relation_key,
                   relation_type, target_source_guid, target_service,
                   resolution_status, resolved_relation_id, metadata_json
               ) VALUES (?, ?, ?, ?, ?, ?, 'unresolved', NULL, ?)""",
            (
                message_source_id,
                import_run_id,
                key,
                relation_type,
                target_source_guid,
                target_service,
                payload,
            ),
        )
    return int(cur.lastrowid)


def resolve_source_relation(
    db: CanonicalDatabase,
    *,
    source_relation_id: int,
    source_message_id: int,
    target_message_id: int,
    canonical_metadata: Mapping[str, Any] | None = None,
) -> int:
    """Bind one source relation occurrence to exactly one canonical relation."""

    row = db.conn.execute(
        """SELECT mrs.relation_type, mrs.resolution_status,
                  mrs.resolved_relation_id, ms.message_id
           FROM message_relation_source mrs
           JOIN message_source ms ON ms.id=mrs.message_source_id
           WHERE mrs.id=?""",
        (source_relation_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown source_relation_id: {source_relation_id}")
    if int(row["message_id"]) != int(source_message_id):
        raise ValueError("source relation belongs to a different canonical source message")

    relation_type = str(row["relation_type"])
    db.add_relation(
        source_message_id,
        target_message_id,
        relation_type,
        canonical_metadata,
    )
    canonical = db.conn.execute(
        """SELECT id FROM message_relation
           WHERE source_message_id=? AND target_message_id=? AND relation_type=?""",
        (source_message_id, target_message_id, relation_type),
    ).fetchone()
    if canonical is None:
        raise RuntimeError("canonical message_relation was not persisted")
    canonical_relation_id = int(canonical["id"])

    existing_relation_id = row["resolved_relation_id"]
    if existing_relation_id is not None and int(existing_relation_id) != canonical_relation_id:
        raise ValueError("source relation is already resolved to a different canonical relation")

    with db.conn:
        db.conn.execute(
            """UPDATE message_relation_source
               SET resolution_status='resolved', resolved_relation_id=?
               WHERE id=?""",
            (canonical_relation_id, source_relation_id),
        )
    return canonical_relation_id
