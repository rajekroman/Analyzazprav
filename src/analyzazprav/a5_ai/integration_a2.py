from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Sequence

from .models import MessageRecord


class A2SourceError(RuntimeError):
    pass


class A2SQLiteMessageSource:
    """Read-only MessageSource for A2's canonical SQLite analytical views."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        if not self.database_path.exists() or not self.database_path.is_file():
            raise A2SourceError(f"A2 database does not exist: {self.database_path}")

    def _connect(self) -> sqlite3.Connection:
        uri = f"file:{self.database_path.as_posix()}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True)
        except sqlite3.Error as exc:
            raise A2SourceError(f"Cannot open A2 database read-only: {exc}") from exc
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _to_utc_us(value: datetime) -> int:
        if value.tzinfo is None:
            raise ValueError("A2SQLiteMessageSource requires timezone-aware datetimes")
        return int(round(value.astimezone(timezone.utc).timestamp() * 1_000_000))

    @staticmethod
    def _from_utc_us(value: int) -> datetime:
        return datetime.fromtimestamp(int(value) / 1_000_000, tz=timezone.utc)

    def list_messages(self, conversation_id: str, start_ts: datetime, end_ts: datetime) -> Sequence[MessageRecord]:
        if end_ts < start_ts:
            raise ValueError("end_ts must be >= start_ts")
        start_us = self._to_utc_us(start_ts)
        end_us = self._to_utc_us(end_ts)
        query = """
            SELECT
                m.id AS message_id,
                m.conversation_id,
                m.sender_id,
                m.sent_at_utc_us,
                m.message_type,
                COALESCE(m.text, '') AS text,
                m.is_edited,
                m.is_deleted,
                (
                    SELECT mr.target_message_id
                    FROM message_relation mr
                    WHERE mr.source_message_id = m.id
                      AND lower(mr.relation_type) LIKE '%reply%'
                    ORDER BY mr.id
                    LIMIT 1
                ) AS reply_to_message_id,
                GROUP_CONCAT(DISTINCT a.mime_type) AS attachment_mime_types
            FROM analysis_messages m
            LEFT JOIN analysis_attachments a ON a.message_id = m.id
            WHERE CAST(m.conversation_id AS TEXT) = ?
              AND m.sent_at_utc_us IS NOT NULL
              AND m.sent_at_utc_us BETWEEN ? AND ?
            GROUP BY
                m.id, m.conversation_id, m.sender_id, m.sent_at_utc_us,
                m.message_type, m.text, m.is_edited, m.is_deleted
            ORDER BY m.sent_at_utc_us, m.id
        """
        try:
            with self._connect() as conn:
                rows = conn.execute(query, (str(conversation_id), start_us, end_us)).fetchall()
        except sqlite3.Error as exc:
            raise A2SourceError(
                "A2 database is missing the expected analysis_messages / analysis_attachments contract"
            ) from exc
        messages: list[MessageRecord] = []
        for row in rows:
            mime_types = tuple(
                value.strip()
                for value in (row["attachment_mime_types"] or "").split(",")
                if value and value.strip()
            )
            message_type = str(row["message_type"] or "text")
            if message_type != "text" and not mime_types:
                mime_types = (message_type,)
            messages.append(
                MessageRecord(
                    id=str(row["message_id"]),
                    conversation_id=str(row["conversation_id"]),
                    participant_id=str(row["sender_id"]) if row["sender_id"] is not None else "unknown",
                    timestamp=self._from_utc_us(row["sent_at_utc_us"]),
                    text=str(row["text"] or ""),
                    reply_to_message_id=str(row["reply_to_message_id"]) if row["reply_to_message_id"] is not None else None,
                    attachment_types=mime_types,
                    edited=bool(row["is_edited"]),
                    deleted=bool(row["is_deleted"]),
                )
            )
        return messages
