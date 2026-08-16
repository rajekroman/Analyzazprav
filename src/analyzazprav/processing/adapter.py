from __future__ import annotations

import json
import sqlite3

from .media import classify_media
from .models import (
    A2Projection,
    AttachmentRef,
    CanonicalMessage,
    CanonicalParticipant,
    MessageRelation,
    ParticipantIdentity,
)


def _parse_source_order(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        is not None
    )


def _columns(conn: sqlite3.Connection, name: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({name})")}


def load_a2_projection(conn: sqlite3.Connection) -> A2Projection:
    """Read the A2 analytical contract without depending on A1 source formats."""
    source_rows: dict[int, tuple[str | None, int | None]] = {}
    for message_id, source_message_id, source_row_id in conn.execute(
        """SELECT message_id, source_message_id, source_row_id
           FROM message_source
           ORDER BY message_id, id"""
    ):
        source_rows.setdefault(
            int(message_id),
            (source_message_id, _parse_source_order(source_row_id)),
        )

    identities_by_participant: dict[int, list[ParticipantIdentity]] = {}
    if _table_exists(conn, "participant_identity"):
        for row in conn.execute(
            """SELECT id, participant_id, identity_type, normalized_value, original_value
               FROM participant_identity
               ORDER BY participant_id, id"""
        ):
            identity_id, participant_id, identity_type, normalized_value, original_value = row
            identities_by_participant.setdefault(int(participant_id), []).append(
                ParticipantIdentity(
                    id=int(identity_id),
                    participant_id=int(participant_id),
                    identity_type=str(identity_type),
                    normalized_value=str(normalized_value),
                    original_value=original_value,
                )
            )

    participant_columns = _columns(conn, "participant")
    canonical_name_expr = "canonical_name" if "canonical_name" in participant_columns else "NULL"
    is_self_expr = "is_self" if "is_self" in participant_columns else "0"
    participants = [
        CanonicalParticipant(
            id=int(participant_id),
            canonical_name=canonical_name,
            is_self=bool(is_self),
            identities=tuple(identities_by_participant.get(int(participant_id), ())),
        )
        for participant_id, canonical_name, is_self in conn.execute(
            f"""SELECT id, {canonical_name_expr}, {is_self_expr}
                FROM participant
                ORDER BY id"""
        )
    ]

    attachment_columns = _columns(conn, "analysis_attachments")
    occurrence_expr = "occurrence_id" if "occurrence_id" in attachment_columns else "NULL"
    occurrence_order = ", occurrence_id" if "occurrence_id" in attachment_columns else ""
    attachments: dict[int, list[AttachmentRef]] = {}
    for row in conn.execute(
        f"""SELECT message_id, attachment_id, sha256, mime_type, size_bytes,
                   filename, availability, position, {occurrence_expr}
            FROM analysis_attachments
            ORDER BY message_id, position, attachment_id{occurrence_order}"""
    ):
        (
            message_id,
            attachment_id,
            sha256_value,
            mime_type,
            size_bytes,
            filename,
            availability,
            position,
            occurrence_id,
        ) = row
        attachments.setdefault(int(message_id), []).append(
            AttachmentRef(
                id=int(attachment_id),
                sha256=sha256_value,
                mime_type=mime_type,
                size_bytes=None if size_bytes is None else int(size_bytes),
                filename=filename,
                availability=str(availability),
                position=None if position is None else int(position),
                media_type=classify_media(mime_type, filename),
                occurrence_id=None if occurrence_id is None else int(occurrence_id),
            )
        )

    analysis_message_columns = _columns(conn, "analysis_messages")
    membership_expr = "membership_id" if "membership_id" in analysis_message_columns else "NULL"
    messages: list[CanonicalMessage] = []
    for row in conn.execute(
        f"""SELECT {membership_expr}, id, conversation_id, sender_id, sent_at_utc_us,
                   timezone_offset_min, message_type, text
            FROM analysis_messages
            ORDER BY conversation_id,
                     CASE WHEN sent_at_utc_us IS NULL THEN 1 ELSE 0 END,
                     sent_at_utc_us,
                     CASE WHEN {membership_expr} IS NULL THEN 1 ELSE 0 END,
                     {membership_expr},
                     id"""
    ):
        (
            membership_id,
            message_id,
            conversation_id,
            sender_id,
            timestamp_us,
            timezone_offset_min,
            message_type,
            text,
        ) = row
        source_message_id, source_order = source_rows.get(int(message_id), (None, None))
        messages.append(
            CanonicalMessage(
                id=int(message_id),
                conversation_id=int(conversation_id),
                sender_id=None if sender_id is None else int(sender_id),
                timestamp_us=None if timestamp_us is None else int(timestamp_us),
                text=text,
                source_message_id=source_message_id,
                source_order=source_order,
                timezone_offset_min=(
                    None if timezone_offset_min is None else int(timezone_offset_min)
                ),
                message_type=str(message_type or "text"),
                attachments=tuple(attachments.get(int(message_id), ())),
                membership_id=None if membership_id is None else int(membership_id),
            )
        )

    relations: list[MessageRelation] = []
    for source_id, target_id, relation_type, metadata_json in conn.execute(
        """SELECT source_message_id, target_message_id, relation_type, metadata_json
           FROM message_relation
           ORDER BY source_message_id, target_message_id, relation_type"""
    ):
        try:
            metadata = json.loads(metadata_json or "{}")
        except json.JSONDecodeError:
            metadata = {"_invalid_metadata_json": metadata_json}
        relations.append(
            MessageRelation(
                int(source_id),
                int(target_id),
                str(relation_type),
                metadata,
            )
        )

    return A2Projection(
        messages=tuple(messages),
        relations=tuple(relations),
        participants=tuple(participants),
    )
