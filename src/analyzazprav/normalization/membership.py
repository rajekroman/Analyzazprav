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

    if participant_ids:
        with db.conn:
            db.conn.executemany(
                """INSERT OR IGNORE INTO conversation_participant(
                       conversation_id, participant_id
                   ) VALUES (?, ?)""",
                [(conversation_id, int(pid)) for pid in participant_ids],
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
    """Store one attachment occurrence without collapsing repeated blobs."""

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

    return attachment_id, occurrence_id, True
