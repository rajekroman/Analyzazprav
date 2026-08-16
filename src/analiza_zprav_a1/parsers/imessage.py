from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

from ..apple_time import apple_timestamp_precision, apple_timestamp_to_iso
from ..jsonsafe import json_safe
from ..models import AttachmentRecord, MessageRecord
from ..text_decode import decode_attributed_body


class IMessageParser:
    def __init__(self, chat_db: Path):
        self.chat_db = chat_db

    def _connect(self) -> sqlite3.Connection:
        uri = f"file:{self.chat_db.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    @staticmethod
    def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}

    @staticmethod
    def _tables(conn: sqlite3.Connection) -> set[str]:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def iter_messages(self) -> Iterator[MessageRecord]:
        with self._connect() as conn:
            tables = self._tables(conn)
            participant_cache: dict[int, list[str]] = {}
            conversation_cache: dict[int, dict[str, object]] = {}
            mcols = self._columns(conn, "message")
            if not mcols:
                raise ValueError("The source DB does not contain an Apple Messages 'message' table")
            if not {"date", "is_from_me"}.issubset(mcols):
                raise ValueError("Unsupported Apple Messages schema: message.date/is_from_me missing")

            text_expr = "m.text AS _text" if "text" in mcols else "NULL AS _text"
            attributed_expr = "m.attributedBody AS _attributedBody" if "attributedBody" in mcols else "NULL AS _attributedBody"
            guid_expr = "m.guid AS _guid" if "guid" in mcols else "NULL AS _guid"
            service_expr = "m.service AS _service" if "service" in mcols else "NULL AS _service"
            reply_expr = "m.thread_originator_guid AS _reply_to_guid" if "thread_originator_guid" in mcols else "NULL AS _reply_to_guid"

            if "handle_id" in mcols and "handle" in tables:
                handle_expr = "h.id AS _sender_handle"
                handle_join = "LEFT JOIN handle h ON h.ROWID=m.handle_id"
            else:
                handle_expr = "NULL AS _sender_handle"
                handle_join = ""

            if "chat_message_join" in tables:
                chat_expr = "cmj.chat_id AS _chat_rowid"
                chat_join = "LEFT JOIN chat_message_join cmj ON cmj.message_id=m.ROWID"
            else:
                chat_expr = "NULL AS _chat_rowid"
                chat_join = ""

            query = f"""
            SELECT m.*, m.ROWID AS _message_rowid,
                   {guid_expr}, {text_expr}, {attributed_expr}, {service_expr},
                   {handle_expr}, {reply_expr}, {chat_expr}
            FROM message m
            {chat_join}
            {handle_join}
            ORDER BY m.date, m.ROWID
            """

            for row in conn.execute(query):
                raw_text = row["_text"]
                text = raw_text
                text_source = "text" if raw_text is not None else None
                if text is None and row["_attributedBody"] is not None:
                    text = decode_attributed_body(row["_attributedBody"])
                    text_source = "attributedBody" if text is not None else None

                message_rowid = int(row["_message_rowid"])
                chat_id = row["_chat_rowid"]
                conversation_source_id = str(chat_id) if chat_id is not None else f"orphan:{message_rowid}"
                participants: list[str] = []
                conversation_metadata: dict[str, object] = {}
                if chat_id is not None:
                    chat_rowid = int(chat_id)
                    if chat_rowid not in participant_cache:
                        participant_cache[chat_rowid] = self._participants_for_chat(conn, chat_rowid, tables)
                    if chat_rowid not in conversation_cache:
                        conversation_cache[chat_rowid] = self._conversation_metadata(conn, chat_rowid, tables)
                    participants = participant_cache[chat_rowid]
                    conversation_metadata = conversation_cache[chat_rowid]

                raw_payload = {
                    key: json_safe(row[key])
                    for key in row.keys()
                    if not key.startswith("_")
                }

                yield MessageRecord(
                    source_message_id=str(message_rowid),
                    source_guid=row["_guid"],
                    conversation_source_id=conversation_source_id,
                    timestamp_raw=row["date"],
                    timestamp_utc=apple_timestamp_to_iso(row["date"]),
                    timestamp_precision=apple_timestamp_precision(row["date"]),
                    sender_handle=row["_sender_handle"],
                    is_from_me=bool(row["is_from_me"]),
                    text=text,
                    raw_text=raw_text,
                    text_source=text_source,
                    service=row["_service"],
                    conversation_participant_handles=list(participants),
                    conversation_metadata=dict(conversation_metadata),
                    reply_to_guid=row["_reply_to_guid"],
                    attachments=self._attachments_for(conn, message_rowid),
                    raw_payload=raw_payload,
                    metadata={},
                )

    @staticmethod
    def _participants_for_chat(
        conn: sqlite3.Connection, chat_id: int, tables: set[str]
    ) -> list[str]:
        if "chat_handle_join" not in tables or "handle" not in tables:
            return []
        rows = conn.execute(
            """
            SELECT h.id
            FROM chat_handle_join chj
            JOIN handle h ON h.ROWID=chj.handle_id
            WHERE chj.chat_id=?
            ORDER BY h.ROWID
            """,
            (chat_id,),
        )
        return [str(row[0]) for row in rows if row[0] is not None]

    @staticmethod
    def _conversation_metadata(
        conn: sqlite3.Connection, chat_id: int, tables: set[str]
    ) -> dict[str, object]:
        if "chat" not in tables:
            return {}
        row = conn.execute(
            "SELECT c.*, c.ROWID AS _chat_rowid FROM chat c WHERE c.ROWID=?",
            (chat_id,),
        ).fetchone()
        if row is None:
            return {}
        return {key: json_safe(row[key]) for key in row.keys() if not key.startswith("_")}

    def _attachments_for(self, conn: sqlite3.Connection, message_id: int) -> list[AttachmentRecord]:
        tables = self._tables(conn)
        if "attachment" not in tables or "message_attachment_join" not in tables:
            return []
        rows = conn.execute(
            """
            SELECT a.* , a.ROWID AS _attachment_rowid
            FROM message_attachment_join maj
            JOIN attachment a ON a.ROWID=maj.attachment_id
            WHERE maj.message_id=?
            ORDER BY a.ROWID
            """,
            (message_id,),
        )
        records: list[AttachmentRecord] = []
        for row in rows:
            keys = set(row.keys())
            records.append(
                AttachmentRecord(
                    source_attachment_id=str(row["_attachment_rowid"]),
                    filename=row["filename"] if "filename" in keys else None,
                    mime_type=row["mime_type"] if "mime_type" in keys else None,
                    transfer_name=row["transfer_name"] if "transfer_name" in keys else None,
                    total_bytes=row["total_bytes"] if "total_bytes" in keys else None,
                    source_path=row["filename"] if "filename" in keys else None,
                    raw_payload={
                        key: json_safe(row[key])
                        for key in row.keys()
                        if not key.startswith("_")
                    },
                )
            )
        return records
