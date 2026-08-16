from __future__ import annotations

import sqlite3
from datetime import datetime


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def materialize_message_features(conn: sqlite3.Connection, session_gap_minutes: int = 120) -> int:
    """Build deterministic per-conversation temporal features.

    A reply turn is defined conservatively: the current sender differs from the
    immediately preceding sender in that same conversation. No semantic AI is used.
    """
    conn.execute("DELETE FROM message_features")
    conversations = [int(r[0]) for r in conn.execute("SELECT id FROM conversations ORDER BY id")]
    written = 0
    gap_seconds = session_gap_minutes * 60

    for conversation_id in conversations:
        rows = conn.execute(
            """
            SELECT m.id, m.sender_participant_id, m.sent_at_utc, m.raw_rowid
            FROM message_conversations mc
            JOIN messages m ON m.id=mc.message_id
            WHERE mc.conversation_id=?
            ORDER BY (m.sent_at_utc IS NULL), m.sent_at_utc, COALESCE(m.raw_rowid, m.id), m.id
            """,
            (conversation_id,),
        ).fetchall()
        previous = None
        session_index = 0
        for sequence, row in enumerate(rows, start=1):
            current_dt = _parse_iso(row["sent_at_utc"])
            reply_to = None
            latency = None
            if previous is not None:
                previous_dt = _parse_iso(previous["sent_at_utc"])
                if current_dt and previous_dt:
                    delta = (current_dt - previous_dt).total_seconds()
                    if delta > gap_seconds:
                        session_index += 1
                    if row["sender_participant_id"] != previous["sender_participant_id"] and delta >= 0:
                        reply_to = int(previous["id"])
                        latency = float(delta)
                elif current_dt != previous_dt:
                    session_index += 1
            conn.execute(
                """
                INSERT INTO message_features(
                    message_id, conversation_id, sequence_in_conversation, session_index,
                    previous_message_id, reply_to_message_id, response_latency_seconds
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(row["id"]), conversation_id, sequence, session_index,
                    int(previous["id"]) if previous is not None else None,
                    reply_to, latency,
                ),
            )
            written += 1
            previous = row
    conn.commit()
    return written
