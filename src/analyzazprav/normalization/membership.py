from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from .database import CanonicalDatabase


def import_source_snapshot(
    db: CanonicalDatabase,
    import_run_id: int,
) -> tuple[str, str | None]:
    row = db.conn.execute(
        "SELECT source_sha256, source_fingerprint FROM import_run WHERE id=?",
        (import_run_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown import_run_id: {import_run_id}")
    source_sha256 = None if not row["source_sha256"] else str(row["source_sha256"])
    if source_sha256:
        return source_sha256, source_sha256
    fingerprint = str(row["source_fingerprint"] or "").strip()
    if not fingerprint:
        raise ValueError("import_run has neither source_sha256 nor source_fingerprint")
    return f"fingerprint:{fingerprint}", None


def _source_conversation_participant_ids(
    db: CanonicalDatabase,
    metadata: Mapping[str, Any] | None,
) -> list[int]:
    """Canonicalize exact source participant handles carried by one chat relation.

    A1 Apple records expose ``participant_handles`` from ``chat_handle_join`` on
    each source conversation relation.  These are strong source identities, not
    display names, so A2 may safely normalize them using the same identity rules
    as message senders.  The original handle list remains untouched in source
    metadata/provenance.
    """

    if not metadata or "participant_handles" not in metadata:
        return []
    raw_handles = metadata.get("participant_handles")
    if raw_handles is None:
        return []
    if not isinstance(raw_handles, list):
        raise ValueError("source conversation participant_handles must be an array")

    result: list[int] = []
    for raw_handle in raw_handles:
        if raw_handle is None:
            continue
        if not isinstance(raw_handle, str):
            raise ValueError("source conversation participant handle must be a string")
        handle = raw_handle.strip()
        if not handle:
            continue
        if "@" in handle:
            identity_type = "email"
        else:
            compact = "".join(ch for ch in handle if ch not in " +()-.")
            identity_type = "phone" if compact.isdigit() and compact else "imessage_handle"
        participant_id = db.get_or_create_participant(
            identity_type=identity_type,
            identity_value=handle,
        )
        if participant_id not in result:
            result.append(participant_id)
    return result


def get_or_create_source_conversation(
    db: CanonicalDatabase,
    *,
    import_run_id: int,
    source_type: str,
    source_conversation_id: str,
    source_sha256: str | None = None,
    canonical_key: str | None = None,
    title: str | None = None,
    conversation_type: str = "unknown",
    service: str | None = None,
    participant_ids: Sequence[int] = (),
    metadata: Mapping[str, Any] | None = None,
) -> tuple[int, int]:
    """Resolve one source conversation without treating DB-local IDs as global.

    Returns ``(conversation_id, conversation_source_id)``. Source identity is
    scoped by an immutable snapshot key. For A1 bundles the raw source SHA-256 is
    the snapshot key; generic/direct imports fall back to their ingest fingerprint.
    Cross-snapshot canonical merging happens only with an explicit canonical key.
    """

    inferred_snapshot_key, inferred_sha256 = import_source_snapshot(db, import_run_id)
    snapshot_key = source_sha256 or inferred_snapshot_key
    raw_sha256 = source_sha256 or inferred_sha256

    row = db.conn.execute(
        """SELECT id, conversation_id
           FROM conversation_source
           WHERE source_type=? AND source_snapshot_key=? AND source_conversation_id=?""",
        (source_type, snapshot_key, source_conversation_id),
    ).fetchone()
    if row is not None:
        conversation_id = int(row["conversation_id"])
        conversation_source_pk = int(row["id"])
    else:
        conversation_id = 0
        if canonical_key is not None:
            canonical = db.conn.execute(
                "SELECT id FROM conversation WHERE canonical_key=?", (canonical_key,)
            ).fetchone()
            if canonical is not None:
                conversation_id = int(canonical["id"])

        with db.conn:
            if not conversation_id:
                cur = db.conn.execute(
                    """INSERT INTO conversation(
                           canonical_key, title, conversation_type, service, metadata_json
                       ) VALUES (?, ?, ?, ?, ?)""",
                    (
                        canonical_key,
                        title,
                        conversation_type,
                        service,
                        json.dumps(
                            metadata or {},
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                )
                conversation_id = int(cur.lastrowid)
            cur = db.conn.execute(
                """INSERT INTO conversation_source(
                       conversation_id, import_run_id, source_type,
                       source_snapshot_key, source_sha256, source_conversation_id,
                       metadata_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    conversation_id,
                    import_run_id,
                    source_type,
                    snapshot_key,
                    raw_sha256,
                    source_conversation_id,
                    json.dumps(
                        metadata or {},
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            )
            conversation_source_pk = int(cur.lastrowid)

    all_participant_ids: list[int] = []
    for participant_id in participant_ids:
        value = int(participant_id)
        if value not in all_participant_ids:
            all_participant_ids.append(value)
    for participant_id in _source_conversation_participant_ids(db, metadata):
        if participant_id not in all_participant_ids:
            all_participant_ids.append(participant_id)

    if all_participant_ids:
        with db.conn:
            db.conn.executemany(
                """INSERT OR IGNORE INTO conversation_participant(
                       conversation_id, participant_id
                   ) VALUES (?, ?)""",
                [(conversation_id, participant_id) for participant_id in all_participant_ids],
            )

    return conversation_id, conversation_source_pk


def message_source_pk(
    db: CanonicalDatabase,
    *,
    import_run_id: int,
    source_record_key: str,
) -> int:
    row = db.conn.execute(
        """SELECT id FROM message_source
           WHERE import_run_id=? AND source_record_key=?
           ORDER BY id LIMIT 1""",
        (import_run_id, source_record_key),
    ).fetchone()
    if row is None:
        raise RuntimeError(
            f"message_source missing for source_record_key={source_record_key!r}"
        )
    return int(row["id"])


def link_message_conversation(
    db: CanonicalDatabase,
    *,
    message_id: int,
    conversation_id: int,
    message_source_id: int,
    conversation_source_id: int,
    position: int | None,
    prefer_primary: bool = False,
    metadata: Mapping[str, Any] | None = None,
) -> int:
    """Persist canonical membership and the exact source relation behind it."""

    existing = db.conn.execute(
        """SELECT id, is_primary FROM message_conversation
           WHERE message_id=? AND conversation_id=?""",
        (message_id, conversation_id),
    ).fetchone()

    if existing is None:
        primary_exists = db.conn.execute(
            """SELECT 1 FROM message_conversation
               WHERE message_id=? AND is_primary=1 LIMIT 1""",
            (message_id,),
        ).fetchone()
        is_primary = int(prefer_primary and primary_exists is None)
        with db.conn:
            cur = db.conn.execute(
                """INSERT INTO message_conversation(
                       message_id, conversation_id, is_primary, metadata_json
                   ) VALUES (?, ?, ?, ?)""",
                (
                    message_id,
                    conversation_id,
                    is_primary,
                    json.dumps(
                        metadata or {},
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            )
            membership_id = int(cur.lastrowid)
            if is_primary:
                db.conn.execute(
                    "UPDATE message SET conversation_id=? WHERE id=?",
                    (conversation_id, message_id),
                )
    else:
        membership_id = int(existing["id"])

    with db.conn:
        db.conn.execute(
            """INSERT OR IGNORE INTO message_source_conversation(
                   message_source_id, conversation_source_id, membership_id,
                   position, metadata_json
               ) VALUES (?, ?, ?, ?, ?)""",
            (
                message_source_id,
                conversation_source_id,
                membership_id,
                position,
                json.dumps(
                    metadata or {},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )
    return membership_id


def _insert_attachment_source(
    db: CanonicalDatabase,
    *,
    attachment_id: int,
    import_run_id: int,
    source_attachment_id: str | None,
    original_filename: str | None,
    original_path: str | None,
    raw_payload: Mapping[str, Any] | None,
    occurrence_id: int,
    source_occurrence_key: str,
) -> None:
    db.conn.execute(
        """INSERT INTO attachment_source(
               attachment_id, import_run_id, source_attachment_id,
               original_filename, original_path, raw_payload_json,
               message_attachment_occurrence_id, source_occurrence_key
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            attachment_id,
            import_run_id,
            source_attachment_id,
            original_filename,
            original_path,
            json.dumps(
                raw_payload or {},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            occurrence_id,
            source_occurrence_key,
        ),
    )


def add_attachment_occurrence(
    db: CanonicalDatabase,
    *,
    message_id: int,
    import_run_id: int,
    source_record_key: str,
    source_attachment_id: str | None,
    position: int,
    sha256_value: str | None = None,
    mime_type: str | None = None,
    size_bytes: int | None = None,
    filename: str | None = None,
    storage_path: str | None = None,
    availability: str = "unknown",
    original_filename: str | None = None,
    original_path: str | None = None,
    raw_payload: Mapping[str, Any] | None = None,
) -> tuple[int, int, bool]:
    """Store one attachment occurrence without collapsing repeated blobs.

    A retry of the same import run returns the existing source provenance row.
    A newer parser run over the same immutable source snapshot reuses the same
    canonical attachment occurrence and adds a new provenance row, even when the
    original source did not provide a content hash.
    """

    source_occurrence_key = (
        f"{source_record_key}:attachment:"
        f"{source_attachment_id if source_attachment_id is not None else position}"
        f":position:{position}"
    )

    existing_source = db.conn.execute(
        """SELECT attachment_id, message_attachment_occurrence_id
           FROM attachment_source
           WHERE import_run_id=? AND source_occurrence_key=?""",
        (import_run_id, source_occurrence_key),
    ).fetchone()
    if existing_source is not None:
        occurrence_id = existing_source["message_attachment_occurrence_id"]
        if occurrence_id is None:
            raise RuntimeError("attachment source occurrence exists without occurrence link")
        return int(existing_source["attachment_id"]), int(occurrence_id), False

    snapshot_key, _ = import_source_snapshot(db, import_run_id)
    previous_source = db.conn.execute(
        """SELECT ats.attachment_id,
                  ats.message_attachment_occurrence_id,
                  a.sha256
           FROM attachment_source ats
           JOIN import_run ir ON ir.id = ats.import_run_id
           JOIN attachment a ON a.id = ats.attachment_id
           WHERE ats.source_occurrence_key=?
             AND COALESCE(ir.source_sha256, 'fingerprint:' || ir.source_fingerprint)=?
           ORDER BY ats.id
           LIMIT 1""",
        (source_occurrence_key, snapshot_key),
    ).fetchone()
    if previous_source is not None:
        occurrence_id = previous_source["message_attachment_occurrence_id"]
        if occurrence_id is None:
            raise RuntimeError("prior attachment provenance has no occurrence link")
        attachment_id = int(previous_source["attachment_id"])
        previous_sha = previous_source["sha256"]
        if sha256_value and previous_sha and str(previous_sha) != sha256_value:
            raise ValueError(
                "Same source attachment occurrence produced conflicting SHA-256 values"
            )
        with db.conn:
            if sha256_value and not previous_sha:
                db.conn.execute(
                    "UPDATE attachment SET sha256=? WHERE id=?",
                    (sha256_value, attachment_id),
                )
            _insert_attachment_source(
                db,
                attachment_id=attachment_id,
                import_run_id=import_run_id,
                source_attachment_id=source_attachment_id,
                original_filename=original_filename,
                original_path=original_path,
                raw_payload=raw_payload,
                occurrence_id=int(occurrence_id),
                source_occurrence_key=source_occurrence_key,
            )
        return attachment_id, int(occurrence_id), True

    attachment_id: int | None = None
    if sha256_value:
        row = db.conn.execute(
            "SELECT id FROM attachment WHERE sha256=? ORDER BY id LIMIT 1",
            (sha256_value,),
        ).fetchone()
        if row is not None:
            attachment_id = int(row["id"])

    with db.conn:
        if attachment_id is None:
            cur = db.conn.execute(
                """INSERT INTO attachment(
                       sha256, mime_type, size_bytes, filename, storage_path, availability
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    sha256_value,
                    mime_type,
                    size_bytes,
                    filename,
                    storage_path,
                    availability,
                ),
            )
            attachment_id = int(cur.lastrowid)

        db.conn.execute(
            """INSERT OR IGNORE INTO message_attachment(message_id, attachment_id, position)
               VALUES (?, ?, ?)""",
            (message_id, attachment_id, position),
        )

        occurrence = db.conn.execute(
            """SELECT id, attachment_id FROM message_attachment_occurrence
               WHERE message_id=? AND position=?""",
            (message_id, position),
        ).fetchone()
        if occurrence is None:
            cur = db.conn.execute(
                """INSERT INTO message_attachment_occurrence(
                       message_id, attachment_id, position, metadata_json
                   ) VALUES (?, ?, ?, '{}')""",
                (message_id, attachment_id, position),
            )
            occurrence_id = int(cur.lastrowid)
        else:
            if int(occurrence["attachment_id"]) != attachment_id:
                raise ValueError(
                    f"Attachment position {position} for message {message_id} "
                    "already points to a different canonical attachment"
                )
            occurrence_id = int(occurrence["id"])

        _insert_attachment_source(
            db,
            attachment_id=attachment_id,
            import_run_id=import_run_id,
            source_attachment_id=source_attachment_id,
            original_filename=original_filename,
            original_path=original_path,
            raw_payload=raw_payload,
            occurrence_id=occurrence_id,
            source_occurrence_key=source_occurrence_key,
        )

    return attachment_id, occurrence_id, True
