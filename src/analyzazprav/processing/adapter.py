from __future__ import annotations

import json
import sqlite3

from .models import A2Projection, CanonicalMessage, MessageRelation


def _parse_source_order(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def load_a2_projection(conn: sqlite3.Connection) -> A2Projection:
    """Read A2 analytical views/tables without depending on A1 source formats."""
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

    attachments: dict[int, list[str]] = {}
    for message_id, attachment_id, sha256_value, availability in conn.execute(
        """SELECT message_id, attachment_id, sha256, availability
           FROM analysis_attachments
           ORDER BY message_id, position, attachment_id"""
    ):
        key = sha256_value or f"attachment:{attachment_id}:{availability}"
        attachments.setdefault(int(message_id), []).append(str(key))

    messages: list[CanonicalMessage] = []
    for row in conn.execute(
        """SELECT id, conversation_id, sender_id, sent_at_utc_us, text
           FROM analysis_messages
           ORDER BY conversation_id,
                    CASE WHEN sent_at_utc_us IS NULL THEN 1 ELSE 0 END,
                    sent_at_utc_us, id"""
    ):
        message_id, conversation_id, sender_id, timestamp_us, text = row
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
                attachment_keys=tuple(attachments.get(int(message_id), ())),
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
            MessageRelation(int(source_id), int(target_id), str(relation_type), metadata)
        )

    return A2Projection(tuple(messages), tuple(relations))
