from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from analyza_zprav.timeutils import apple_timestamp_to_datetime, to_iso_z


@dataclass(frozen=True)
class ImportSummary:
    source_message_count: int
    imported_message_count: int
    duplicate_message_count: int
    error_count: int
    source_id: int
    import_run_id: int


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.DatabaseError:
        return set()


def _file_fingerprint(path: Path) -> str:
    stat = path.stat()
    material = f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode()
    return hashlib.sha256(material).hexdigest()


def _participant_key(address: str | None, is_me: bool) -> str:
    if is_me:
        return "me"
    return f"address:{(address or 'unknown').strip().lower()}"


def _upsert_participant(dst: sqlite3.Connection, *, address: str | None, is_me: bool) -> int:
    key = _participant_key(address, is_me)
    dst.execute(
        """
        INSERT INTO participants(canonical_key, display_name, address, is_me)
        VALUES(?, ?, ?, ?)
        ON CONFLICT(canonical_key) DO UPDATE SET
          address=COALESCE(participants.address, excluded.address),
          is_me=MAX(participants.is_me, excluded.is_me)
        """,
        (key, "Já" if is_me else address, address, int(is_me)),
    )
    return int(dst.execute("SELECT id FROM participants WHERE canonical_key=?", (key,)).fetchone()[0])


def _conversation_external_id(chat_rowid: int | None, chat_guid: str | None) -> str:
    if chat_guid:
        return f"chat-guid:{chat_guid}"
    if chat_rowid is not None:
        return f"chat-rowid:{chat_rowid}"
    return "unassigned"


def _ensure_conversation(
    dst: sqlite3.Connection,
    source_id: int,
    *,
    chat_rowid: int | None,
    chat_guid: str | None,
    display_name: str | None,
    service: str | None,
) -> int:
    external_id = _conversation_external_id(chat_rowid, chat_guid)
    dst.execute(
        """
        INSERT INTO conversations(source_id, external_id, display_name, service)
        VALUES(?, ?, ?, ?)
        ON CONFLICT(source_id, external_id) DO UPDATE SET
          display_name=COALESCE(conversations.display_name, excluded.display_name),
          service=COALESCE(conversations.service, excluded.service)
        """,
        (source_id, external_id, display_name, service),
    )
    return int(dst.execute(
        "SELECT id FROM conversations WHERE source_id=? AND external_id=?",
        (source_id, external_id),
    ).fetchone()[0])


def import_chat_db(source_path: str | Path, dst: sqlite3.Connection) -> ImportSummary:
    source = Path(source_path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(source)

    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row

    tables = {row[0] for row in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required = {"message", "chat_message_join", "chat", "handle"}
    missing = required - tables
    if missing:
        raise ValueError(f"Unsupported chat.db: missing tables {sorted(missing)}")

    canonical_path = str(source)
    fingerprint = _file_fingerprint(source)
    dst.execute(
        """
        INSERT INTO sources(kind, canonical_path, fingerprint)
        VALUES('imessage_chat_db', ?, ?)
        ON CONFLICT(kind, canonical_path) DO UPDATE SET fingerprint=excluded.fingerprint
        """,
        (canonical_path, fingerprint),
    )
    source_id = int(dst.execute(
        "SELECT id FROM sources WHERE kind='imessage_chat_db' AND canonical_path=?",
        (canonical_path,),
    ).fetchone()[0])
    dst.execute("INSERT INTO import_runs(source_id) VALUES(?)", (source_id,))
    run_id = int(dst.execute("SELECT last_insert_rowid()").fetchone()[0])
    dst.commit()

    source_count = int(src.execute("SELECT COUNT(*) FROM message").fetchone()[0])
    imported = duplicates = errors = 0
    me_id = _upsert_participant(dst, address=None, is_me=True)

    message_cols = _table_columns(src, "message")
    handle_cols = _table_columns(src, "handle")
    chat_cols = _table_columns(src, "chat")

    def mcol(name: str, fallback: str = "NULL") -> str:
        return f"m.{name}" if name in message_cols else fallback

    h_address = "h.id" if "id" in handle_cols else "NULL"

    try:
        # 1) Import every source message exactly once, independent of chat joins.
        message_query = f"""
            SELECT m.ROWID AS message_rowid,
                   {mcol('guid', "'rowid:' || m.ROWID")} AS message_guid,
                   {mcol('text')} AS text,
                   {mcol('date')} AS date,
                   {mcol('is_from_me', '0')} AS is_from_me,
                   {mcol('service')} AS message_service,
                   {mcol('item_type')} AS item_type,
                   {mcol('associated_message_guid')} AS associated_message_guid,
                   {mcol('cache_has_attachments', '0')} AS cache_has_attachments,
                   {h_address} AS handle_address
            FROM message m
            LEFT JOIN handle h ON h.ROWID = m.handle_id
            ORDER BY m.ROWID
        """
        sender_by_source_rowid: dict[int, int] = {}
        for row in src.execute(message_query):
            is_from_me = bool(row["is_from_me"])
            sender_id = me_id if is_from_me else _upsert_participant(
                dst, address=row["handle_address"], is_me=False
            )
            sender_by_source_rowid[int(row["message_rowid"])] = sender_id
            external_id = str(row["message_guid"] or f"rowid:{row['message_rowid']}")
            before = dst.total_changes
            dst.execute(
                """
                INSERT OR IGNORE INTO messages(
                    source_id, external_id, sender_participant_id, sent_at_utc, text,
                    is_from_me, service, message_type, associated_message_guid,
                    has_attachments, raw_rowid
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id, external_id, sender_id,
                    to_iso_z(apple_timestamp_to_datetime(row["date"])), row["text"],
                    int(is_from_me), row["message_service"], row["item_type"],
                    row["associated_message_guid"], int(bool(row["cache_has_attachments"])),
                    row["message_rowid"],
                ),
            )
            if dst.total_changes == before:
                duplicates += 1
            else:
                imported += 1

        # 2) Preserve every message↔chat relation. conversation_id stores a deterministic primary chat.
        c_guid = "c.guid" if "guid" in chat_cols else "NULL"
        c_name = "c.display_name" if "display_name" in chat_cols else "NULL"
        c_service = "c.service_name" if "service_name" in chat_cols else "NULL"
        relation_query = f"""
            SELECT cmj.message_id AS message_rowid, c.ROWID AS chat_rowid,
                   {c_guid} AS chat_guid, {c_name} AS chat_name, {c_service} AS chat_service
            FROM chat_message_join cmj
            JOIN chat c ON c.ROWID = cmj.chat_id
            ORDER BY cmj.message_id, c.ROWID
        """
        linked_source_rows: set[int] = set()
        for rel in src.execute(relation_query):
            source_rowid = int(rel["message_rowid"])
            msg = dst.execute(
                "SELECT id, conversation_id FROM messages WHERE source_id=? AND raw_rowid=?",
                (source_id, source_rowid),
            ).fetchone()
            if not msg:
                continue
            conv_id = _ensure_conversation(
                dst, source_id,
                chat_rowid=rel["chat_rowid"], chat_guid=rel["chat_guid"],
                display_name=rel["chat_name"], service=rel["chat_service"],
            )
            is_primary = 1 if msg["conversation_id"] is None else 0
            dst.execute(
                "INSERT OR IGNORE INTO message_conversations(message_id, conversation_id, is_primary) VALUES(?, ?, ?)",
                (int(msg["id"]), conv_id, is_primary),
            )
            if is_primary:
                dst.execute("UPDATE messages SET conversation_id=? WHERE id=?", (conv_id, int(msg["id"])))
            linked_source_rows.add(source_rowid)
            sender_id = sender_by_source_rowid.get(source_rowid)
            if sender_id is not None:
                dst.execute(
                    "INSERT OR IGNORE INTO conversation_participants(conversation_id, participant_id) VALUES(?, ?)",
                    (conv_id, sender_id),
                )
            dst.execute(
                "INSERT OR IGNORE INTO conversation_participants(conversation_id, participant_id) VALUES(?, ?)",
                (conv_id, me_id),
            )

        # 3) Never drop chat-less source messages: retain them under a synthetic source-local bucket.
        unlinked = [r for r in sender_by_source_rowid if r not in linked_source_rows]
        if unlinked:
            unassigned_id = _ensure_conversation(
                dst, source_id, chat_rowid=None, chat_guid=None,
                display_name="Unassigned source messages", service=None,
            )
            for source_rowid in unlinked:
                msg = dst.execute(
                    "SELECT id FROM messages WHERE source_id=? AND raw_rowid=?",
                    (source_id, source_rowid),
                ).fetchone()
                if not msg:
                    continue
                msg_id = int(msg[0])
                dst.execute(
                    "INSERT OR IGNORE INTO message_conversations(message_id, conversation_id, is_primary) VALUES(?, ?, 1)",
                    (msg_id, unassigned_id),
                )
                dst.execute("UPDATE messages SET conversation_id=? WHERE id=?", (unassigned_id, msg_id))
                dst.execute(
                    "INSERT OR IGNORE INTO conversation_participants(conversation_id, participant_id) VALUES(?, ?)",
                    (unassigned_id, sender_by_source_rowid[source_rowid]),
                )
                dst.execute(
                    "INSERT OR IGNORE INTO conversation_participants(conversation_id, participant_id) VALUES(?, ?)",
                    (unassigned_id, me_id),
                )

        # 4) Import attachment metadata and exact message links.
        if {"attachment", "message_attachment_join"}.issubset(tables):
            a_cols = _table_columns(src, "attachment")
            def acol(name: str, fallback: str = "NULL") -> str:
                return f"a.{name}" if name in a_cols else fallback
            attachment_query = f"""
                SELECT a.ROWID AS attachment_rowid,
                       {acol('guid', "'rowid:' || a.ROWID")} AS attachment_guid,
                       {acol('filename')} AS filename,
                       {acol('mime_type')} AS mime_type,
                       {acol('transfer_name')} AS transfer_name,
                       {acol('total_bytes')} AS total_bytes,
                       {acol('is_sticker', '0')} AS is_sticker,
                       maj.message_id AS source_message_rowid
                FROM attachment a
                JOIN message_attachment_join maj ON maj.attachment_id = a.ROWID
            """
            for a in src.execute(attachment_query):
                ext = str(a["attachment_guid"] or f"rowid:{a['attachment_rowid']}")
                dst.execute(
                    """
                    INSERT INTO attachments(source_id, external_id, filename, mime_type, transfer_name, total_bytes, is_sticker)
                    VALUES(?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_id, external_id) DO UPDATE SET
                      filename=COALESCE(attachments.filename, excluded.filename),
                      mime_type=COALESCE(attachments.mime_type, excluded.mime_type),
                      transfer_name=COALESCE(attachments.transfer_name, excluded.transfer_name),
                      total_bytes=COALESCE(attachments.total_bytes, excluded.total_bytes)
                    """,
                    (source_id, ext, a["filename"], a["mime_type"], a["transfer_name"], a["total_bytes"], int(bool(a["is_sticker"]))),
                )
                attachment_id = int(dst.execute(
                    "SELECT id FROM attachments WHERE source_id=? AND external_id=?", (source_id, ext)
                ).fetchone()[0])
                msg = dst.execute(
                    "SELECT id FROM messages WHERE source_id=? AND raw_rowid=?",
                    (source_id, a["source_message_rowid"]),
                ).fetchone()
                if msg:
                    dst.execute(
                        "INSERT OR IGNORE INTO message_attachments(message_id, attachment_id) VALUES(?, ?)",
                        (int(msg[0]), attachment_id),
                    )

        details = {"source": canonical_path, "fingerprint": fingerprint}
        dst.execute(
            """
            UPDATE import_runs SET finished_at=CURRENT_TIMESTAMP,
              source_message_count=?, imported_message_count=?, duplicate_message_count=?,
              error_count=?, status='completed', details_json=? WHERE id=?
            """,
            (source_count, imported, duplicates, errors, json.dumps(details), run_id),
        )
        dst.commit()
    except Exception as exc:
        dst.rollback()
        dst.execute(
            "UPDATE import_runs SET finished_at=CURRENT_TIMESTAMP, error_count=?, status='failed', details_json=? WHERE id=?",
            (errors + 1, json.dumps({"error": repr(exc)}), run_id),
        )
        dst.commit()
        raise
    finally:
        src.close()

    return ImportSummary(source_count, imported, duplicates, errors, source_id, run_id)
