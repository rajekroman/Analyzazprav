from __future__ import annotations

import json
import sqlite3

from .media import classify_media
from .models import A2Projection, AttachmentRef, CanonicalMessage, CanonicalParticipant, MessageRelation, ParticipantIdentity


def _parse_source_order(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _columns(conn: sqlite3.Connection, name: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({name})")}


def load_a2_projection(conn: sqlite3.Connection) -> A2Projection:
    """Read the A2 v5 analytical contract without depending on A1 formats."""
    source_rows: dict[int, list[tuple[str | None, int | None, str | None]]] = {}
    for message_id, source_message_id, source_row_id, source_record_key in conn.execute(
        """SELECT message_id, source_message_id, source_row_id, source_record_key
           FROM message_source
           ORDER BY message_id,
                    CASE WHEN source_record_key IS NULL THEN 1 ELSE 0 END,
                    source_record_key, id"""
    ):
        source_rows.setdefault(int(message_id), []).append((
            None if source_message_id is None else str(source_message_id),
            _parse_source_order(source_row_id),
            None if source_record_key is None else str(source_record_key),
        ))

    identities_by_participant: dict[int, list[ParticipantIdentity]] = {}
    if _table_exists(conn, "participant_identity"):
        for row in conn.execute(
            """SELECT id, participant_id, identity_type, normalized_value, original_value
               FROM participant_identity ORDER BY participant_id, id"""
        ):
            identity_id, participant_id, identity_type, normalized_value, original_value = row
            identities_by_participant.setdefault(int(participant_id), []).append(
                ParticipantIdentity(
                    int(identity_id), int(participant_id), str(identity_type),
                    str(normalized_value), None if original_value is None else str(original_value),
                )
            )

    participant_columns = _columns(conn, "participant")
    name_expr = "canonical_name" if "canonical_name" in participant_columns else "NULL"
    self_expr = "is_self" if "is_self" in participant_columns else "0"
    participants = tuple(
        CanonicalParticipant(
            int(participant_id),
            None if canonical_name is None else str(canonical_name),
            bool(is_self),
            tuple(identities_by_participant.get(int(participant_id), ())),
        )
        for participant_id, canonical_name, is_self in conn.execute(
            f"SELECT id, {name_expr}, {self_expr} FROM participant ORDER BY id"
        )
    )

    attachments: dict[int, list[AttachmentRef]] = {}
    for row in conn.execute(
        """SELECT message_id, attachment_id, sha256, mime_type, size_bytes,
                  filename, availability, position
           FROM analysis_attachments
           ORDER BY message_id, CASE WHEN position IS NULL THEN 1 ELSE 0 END,
                    position, occurrence_id"""
    ):
        message_id, attachment_id, sha256_value, mime_type, size_bytes, filename, availability, position = row
        attachments.setdefault(int(message_id), []).append(
            AttachmentRef(
                id=int(attachment_id), sha256=sha256_value, mime_type=mime_type,
                size_bytes=None if size_bytes is None else int(size_bytes), filename=filename,
                availability=str(availability), position=None if position is None else int(position),
                media_type=classify_media(mime_type, filename),
            )
        )

    messages: list[CanonicalMessage] = []
    for row in conn.execute(
        """SELECT membership_id, id, conversation_id, sender_id,
                  sent_at_utc_us, timezone_offset_min, message_type, text
           FROM analysis_messages
           ORDER BY conversation_id, CASE WHEN sent_at_utc_us IS NULL THEN 1 ELSE 0 END,
                    sent_at_utc_us, membership_id"""
    ):
        membership_id, message_id, conversation_id, sender_id, timestamp_us, timezone_offset_min, message_type, text = row
        provenance = source_rows.get(int(message_id), [])
        source_message_id = next((item[0] for item in provenance if item[0] is not None), None)
        source_orders = [item[1] for item in provenance if item[1] is not None]
        source_record_keys = tuple(dict.fromkeys(item[2] for item in provenance if item[2] is not None))
        messages.append(CanonicalMessage(
            membership_id=int(membership_id), id=int(message_id), conversation_id=int(conversation_id),
            sender_id=None if sender_id is None else int(sender_id),
            timestamp_us=None if timestamp_us is None else int(timestamp_us), text=text,
            source_message_id=source_message_id, source_record_keys=source_record_keys,
            source_order=min(source_orders) if source_orders else None,
            timezone_offset_min=None if timezone_offset_min is None else int(timezone_offset_min),
            message_type=str(message_type or "text"), attachments=tuple(attachments.get(int(message_id), ())),
        ))

    relations: list[MessageRelation] = []
    for source_id, target_id, relation_type, metadata_json in conn.execute(
        """SELECT source_message_id, target_message_id, relation_type, metadata_json
           FROM message_relation ORDER BY source_message_id, target_message_id, relation_type"""
    ):
        try:
            metadata = json.loads(metadata_json or "{}")
        except json.JSONDecodeError:
            metadata = {"_invalid_metadata_json": metadata_json}
        relations.append(MessageRelation(int(source_id), int(target_id), str(relation_type), metadata))

    return A2Projection(tuple(messages), tuple(relations), participants)
