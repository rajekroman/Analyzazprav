from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Sequence

from .models import MessageRecord


class A2SourceError(RuntimeError):
    pass


def _split_distinct(value: object) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    return tuple(sorted({part for part in str(value).split(",") if part}))


class A2SQLiteMessageSource:
    """Read-only A5 MessageSource over A2/A3.

    Production databases preserve membership and source provenance and use A3
    v5 resolved sender identity. Minimal historical unit fixtures are detected
    structurally and remain readable with empty provenance; they are never
    mistaken for the current production contract.
    """

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
        conn.execute("PRAGMA query_only=ON")
        return conn

    @staticmethod
    def _to_utc_us(value: datetime) -> int:
        if value.tzinfo is None:
            raise ValueError("A2SQLiteMessageSource requires timezone-aware datetimes")
        return int(round(value.astimezone(timezone.utc).timestamp() * 1_000_000))

    @staticmethod
    def _from_utc_us(value: int) -> datetime:
        return datetime.fromtimestamp(int(value) / 1_000_000, tz=timezone.utc)

    @staticmethod
    def _object_exists(conn: sqlite3.Connection, name: str, object_type: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type=? AND name=?",
            (object_type, name),
        ).fetchone() is not None

    @staticmethod
    def _columns(conn: sqlite3.Connection, name: str) -> set[str]:
        try:
            return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({name})")}
        except sqlite3.Error:
            return set()

    def _participant_contract(self, conn: sqlite3.Connection) -> tuple[str, str]:
        has_resolution_tables = self._object_exists(conn, "resolved_participant", "table")
        has_resolved_view = self._object_exists(
            conn, "analysis_processed_messages_resolved_latest", "view"
        )
        if has_resolution_tables and not has_resolved_view:
            raise A2SourceError(
                "A5 requires A3 v5 resolved-sender view when participant-resolution sidecars exist"
            )
        if has_resolved_view:
            return (
                "JOIN analysis_processed_messages_resolved_latest rpm "
                "ON rpm.membership_id=m.membership_id "
                "AND rpm.message_id=m.id "
                "AND rpm.conversation_id=m.conversation_id",
                "rpm.resolved_sender_id",
            )
        return ("", "m.sender_id")

    def _contract_expressions(self, conn: sqlite3.Connection) -> dict[str, str]:
        message_columns = self._columns(conn, "analysis_messages")
        has_membership = "membership_id" in message_columns
        has_sources = self._object_exists(conn, "message_source", "table")
        has_import_run = self._object_exists(conn, "import_run", "table")
        has_attachments = self._object_exists(conn, "analysis_attachments", "view") or self._object_exists(conn, "analysis_attachments", "table")
        has_relations = self._object_exists(conn, "message_relation", "table")

        return {
            "membership": "m.membership_id" if has_membership else "NULL",
            "reply": (
                "(SELECT mr.target_message_id FROM message_relation mr "
                " WHERE mr.source_message_id=m.id AND lower(mr.relation_type) LIKE '%reply%' "
                " ORDER BY mr.id LIMIT 1)"
                if has_relations
                else "NULL"
            ),
            "attachments": (
                "(SELECT GROUP_CONCAT(DISTINCT a.mime_type) FROM analysis_attachments a "
                " WHERE a.message_id=m.id AND a.mime_type IS NOT NULL)"
                if has_attachments
                else "NULL"
            ),
            "record_keys": (
                "(SELECT GROUP_CONCAT(DISTINCT ms.source_record_key) FROM message_source ms "
                " WHERE ms.message_id=m.id AND ms.source_record_key IS NOT NULL)"
                if has_sources and "source_record_key" in self._columns(conn, "message_source")
                else "NULL"
            ),
            "snapshot_keys": (
                "(SELECT GROUP_CONCAT(DISTINCT COALESCE(ir.source_sha256, 'fingerprint:' || ir.source_fingerprint)) "
                " FROM message_source ms JOIN import_run ir ON ir.id=ms.import_run_id "
                " WHERE ms.message_id=m.id)"
                if has_sources and has_import_run
                else "NULL"
            ),
            "parser_versions": (
                "(SELECT GROUP_CONCAT(DISTINCT ir.parser_version) "
                " FROM message_source ms JOIN import_run ir ON ir.id=ms.import_run_id "
                " WHERE ms.message_id=m.id AND ir.parser_version IS NOT NULL)"
                if has_sources and has_import_run
                else "NULL"
            ),
            "is_edited": "m.is_edited" if "is_edited" in message_columns else "0",
            "is_deleted": "m.is_deleted" if "is_deleted" in message_columns else "0",
            "production": "1" if has_membership and has_sources and has_import_run else "0",
        }

    def list_messages(
        self,
        conversation_id: str,
        start_ts: datetime,
        end_ts: datetime,
    ) -> Sequence[MessageRecord]:
        if end_ts < start_ts:
            raise ValueError("end_ts must be >= start_ts")
        start_us = self._to_utc_us(start_ts)
        end_us = self._to_utc_us(end_ts)
        try:
            with self._connect() as conn:
                joins, participant_expr = self._participant_contract(conn)
                expr = self._contract_expressions(conn)
                query = f"""
                    SELECT
                        m.id AS message_id,
                        {expr['membership']} AS membership_id,
                        m.conversation_id,
                        m.sender_id AS raw_sender_id,
                        {participant_expr} AS participant_id,
                        m.sent_at_utc_us,
                        m.message_type,
                        COALESCE(m.text, '') AS text,
                        {expr['is_edited']} AS is_edited,
                        {expr['is_deleted']} AS is_deleted,
                        {expr['reply']} AS reply_to_message_id,
                        {expr['attachments']} AS attachment_mime_types,
                        {expr['record_keys']} AS source_record_keys,
                        {expr['snapshot_keys']} AS source_snapshot_keys,
                        {expr['parser_versions']} AS source_parser_versions
                    FROM analysis_messages m
                    {joins}
                    WHERE CAST(m.conversation_id AS TEXT) = ?
                      AND m.sent_at_utc_us IS NOT NULL
                      AND m.sent_at_utc_us BETWEEN ? AND ?
                    ORDER BY m.sent_at_utc_us, m.id
                """
                rows = conn.execute(
                    query,
                    (str(conversation_id), start_us, end_us),
                ).fetchall()
                production_contract = expr["production"] == "1"
        except sqlite3.Error as exc:
            raise A2SourceError(
                "A2/A3 database is missing the expected message contract"
            ) from exc

        messages: list[MessageRecord] = []
        for row in rows:
            if row["raw_sender_id"] is not None and row["participant_id"] is None:
                raise A2SourceError(
                    f"A3 resolved sender missing for membership {row['membership_id']}"
                )
            record_keys = _split_distinct(row["source_record_keys"])
            snapshot_keys = _split_distinct(row["source_snapshot_keys"])
            if production_contract and (not record_keys or not snapshot_keys):
                raise A2SourceError(
                    f"A5 production context lacks source provenance for message {row['message_id']}"
                )
            mime_types = _split_distinct(row["attachment_mime_types"])
            message_type = str(row["message_type"] or "text")
            if message_type != "text" and not mime_types:
                mime_types = (message_type,)
            messages.append(
                MessageRecord(
                    id=str(row["message_id"]),
                    membership_id=(
                        str(row["membership_id"])
                        if row["membership_id"] is not None
                        else None
                    ),
                    conversation_id=str(row["conversation_id"]),
                    participant_id=(
                        str(row["participant_id"])
                        if row["participant_id"] is not None
                        else "unknown"
                    ),
                    timestamp=self._from_utc_us(row["sent_at_utc_us"]),
                    text=str(row["text"] or ""),
                    reply_to_message_id=(
                        str(row["reply_to_message_id"])
                        if row["reply_to_message_id"] is not None
                        else None
                    ),
                    attachment_types=mime_types,
                    edited=bool(row["is_edited"]),
                    deleted=bool(row["is_deleted"]),
                    source_record_keys=record_keys,
                    source_snapshot_keys=snapshot_keys,
                    source_parser_versions=_split_distinct(row["source_parser_versions"]),
                )
            )
        return messages

    def context_warnings(
        self,
        conversation_id: str,
        start_ts: datetime,
        end_ts: datetime,
    ) -> Sequence[str]:
        del start_ts, end_ts
        warnings: list[str] = []
        try:
            with self._connect() as conn:
                unknown_time = int(
                    conn.execute(
                        """SELECT COUNT(*) FROM analysis_messages
                           WHERE CAST(conversation_id AS TEXT)=?
                             AND sent_at_utc_us IS NULL""",
                        (str(conversation_id),),
                    ).fetchone()[0]
                )
                if unknown_time:
                    warnings.append(
                        f"{unknown_time} conversation membership(s) have unknown timestamp and "
                        "cannot be placed in A5 temporal context without guessing."
                    )
                if self._object_exists(conn, "message_source", "table"):
                    source_columns = self._columns(conn, "message_source")
                    if "source_record_key" in source_columns:
                        missing_provenance = int(
                            conn.execute(
                                """SELECT COUNT(*)
                                   FROM analysis_messages am
                                   WHERE CAST(am.conversation_id AS TEXT)=?
                                     AND NOT EXISTS (
                                         SELECT 1 FROM message_source ms
                                         WHERE ms.message_id=am.id
                                           AND ms.source_record_key IS NOT NULL
                                     )""",
                                (str(conversation_id),),
                            ).fetchone()[0]
                        )
                        if missing_provenance:
                            warnings.append(
                                f"{missing_provenance} conversation membership(s) lack source_record_key provenance."
                            )
                else:
                    warnings.append(
                        "Legacy A2 fixture does not expose message_source provenance."
                    )
        except sqlite3.Error as exc:
            raise A2SourceError(f"Cannot audit A5 context quality: {exc}") from exc
        return tuple(warnings)
