from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping

from .database import CanonicalDatabase


def _json(value: Mapping[str, Any] | None) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_scalar(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _relation_key(
    *,
    position: int | None,
    relation_type: str,
    target_identifier_type: str,
    target_identifier_value: str,
    target_service: str | None,
    source_relation_type: str | None,
) -> str:
    payload = {
        "position": position,
        "relation_type": relation_type,
        "target_identifier_type": target_identifier_type,
        "target_identifier_value": target_identifier_value,
        "target_service": target_service,
        "source_relation_type": source_relation_type,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return sha256(raw).hexdigest()


def record_source_relation(
    db: CanonicalDatabase,
    *,
    message_source_id: int,
    relation_type: str,
    target_identifier_type: str,
    target_identifier_value: str,
    target_service: str | None = None,
    source_relation_type: str | None = None,
    position: int | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> int:
    """Persist one exact source relation occurrence before canonical resolution.

    This function deliberately does not interpret source-specific numeric relation
    codes. ``relation_type`` must already be source-evidenced by A1 (or use the
    neutral ``source_association`` type). The target source identifier is
    preserved even when no canonical message currently matches it.
    """

    relation_type = relation_type.strip()
    target_identifier_type = target_identifier_type.strip()
    if not relation_type:
        raise ValueError("relation_type is required")
    if not target_identifier_type:
        raise ValueError("target_identifier_type is required")
    if not target_identifier_value.strip():
        raise ValueError("target_identifier_value is required")

    source_row = db.conn.execute(
        "SELECT 1 FROM message_source WHERE id=?",
        (int(message_source_id),),
    ).fetchone()
    if source_row is None:
        raise ValueError(f"Unknown message_source_id: {message_source_id}")

    relation_key = _relation_key(
        position=position,
        relation_type=relation_type,
        target_identifier_type=target_identifier_type,
        target_identifier_value=target_identifier_value,
        target_service=target_service,
        source_relation_type=source_relation_type,
    )
    with db.conn:
        db.conn.execute(
            """INSERT OR IGNORE INTO message_relation_source(
                   message_source_id, relation_key, position, relation_type,
                   target_identifier_type, target_identifier_value, target_service,
                   source_relation_type, metadata_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(message_source_id),
                relation_key,
                position,
                relation_type,
                target_identifier_type,
                target_identifier_value,
                target_service,
                source_relation_type,
                _json(metadata),
            ),
        )
    row = db.conn.execute(
        """SELECT id FROM message_relation_source
           WHERE message_source_id=? AND relation_key=?""",
        (int(message_source_id), relation_key),
    ).fetchone()
    if row is None:
        raise RuntimeError("message_relation_source insert disappeared")
    return int(row["id"])


def record_apple_associated_message(
    db: CanonicalDatabase,
    *,
    message_source_id: int,
    metadata: Mapping[str, Any] | None,
    target_service: str | None,
) -> int | None:
    """Persist A1's conservative Apple associated-message projection neutrally.

    A1 intentionally preserves Apple's exact associated-message target string and
    raw type without claiming that the row is a Tapback/reaction or stripping
    part prefixes such as ``p:0/``. A2 follows that contract: the relation is a
    neutral ``source_association`` and its identifier kind is deliberately not
    ``guid``, so canonical resolution is not attempted automatically.
    """

    if not metadata:
        return None
    raw = metadata.get("apple_associated_message")
    if not isinstance(raw, Mapping):
        return None
    target = raw.get("associated_message_guid")
    if target in (None, ""):
        return None

    source_relation_type = None
    if "associated_message_type" in raw:
        source_relation_type = _json_scalar(raw.get("associated_message_type"))

    return record_source_relation(
        db,
        message_source_id=message_source_id,
        relation_type="source_association",
        target_identifier_type="apple_associated_message_guid",
        target_identifier_value=str(target),
        target_service=target_service,
        source_relation_type=source_relation_type,
        position=0,
        metadata={
            "source": "a1.metadata.apple_associated_message",
            "source_field": "metadata.apple_associated_message",
            "apple_associated_message": dict(raw),
        },
    )


def resolve_source_relation(db: CanonicalDatabase, relation_source_id: int) -> bool:
    """Resolve one source relation when its target identity is currently known.

    Only explicit canonical GUID targets are resolved. Apple associated-message
    target strings and other source-specific identifier kinds remain auditable
    and unresolved until a future verified mapping contract exists.
    """

    row = db.conn.execute(
        """SELECT mrs.id, mrs.relation_type, mrs.target_identifier_type,
                  mrs.target_identifier_value, mrs.target_service,
                  mrs.canonical_relation_id, ms.message_id AS source_message_id
           FROM message_relation_source mrs
           JOIN message_source ms ON ms.id=mrs.message_source_id
           WHERE mrs.id=?""",
        (int(relation_source_id),),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown relation_source_id: {relation_source_id}")
    if row["canonical_relation_id"] is not None:
        return True
    if row["target_identifier_type"] != "guid":
        return False

    target_message_id = db.find_message_by_guid(
        str(row["target_identifier_value"]),
        None if row["target_service"] is None else str(row["target_service"]),
    )
    if target_message_id is None:
        return False

    source_message_id = int(row["source_message_id"])
    relation_type = str(row["relation_type"])
    db.add_relation(
        source_message_id,
        target_message_id,
        relation_type,
        {
            "source": "message_relation_source",
            "target_identifier_type": "guid",
            "target_identifier_value": str(row["target_identifier_value"]),
        },
    )
    canonical = db.conn.execute(
        """SELECT id FROM message_relation
           WHERE source_message_id=? AND target_message_id=? AND relation_type=?""",
        (source_message_id, target_message_id, relation_type),
    ).fetchone()
    if canonical is None:
        raise RuntimeError("Canonical message relation insert disappeared")
    with db.conn:
        db.conn.execute(
            "UPDATE message_relation_source SET canonical_relation_id=? WHERE id=?",
            (int(canonical["id"]), int(relation_source_id)),
        )
    return True


def resolve_pending_relation_sources(db: CanonicalDatabase) -> int:
    """Best-effort deterministic reconciliation of previously unresolved facts."""

    ids = [
        int(row["id"])
        for row in db.conn.execute(
            """SELECT id FROM message_relation_source
               WHERE canonical_relation_id IS NULL
               ORDER BY id"""
        )
    ]
    resolved = 0
    for relation_source_id in ids:
        if resolve_source_relation(db, relation_source_id):
            resolved += 1
    return resolved
