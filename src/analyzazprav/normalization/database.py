from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class ImportRunResult:
    id: int
    already_imported: bool


@dataclass(frozen=True)
class MessageInput:
    import_run_id: int
    source_type: str
    conversation_id: int
    sender_id: int | None
    sent_at_utc_us: int | None
    direction: str = "unknown"
    message_type: str = "text"
    text: str | None = None
    service: str | None = None
    canonical_guid: str | None = None
    timezone_offset_min: int | None = None
    timestamp_precision: str = "unknown"
    timestamp_quality: str = "unknown"
    source_message_id: str | None = None
    source_conversation_id: str | None = None
    source_row_id: str | None = None
    raw_timestamp: str | None = None
    raw_text: str | None = None
    raw_payload: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] | None = None


class CanonicalDatabase:
    """Authoritative SQLite store for the A2 normalization layer.

    A1 importers should pass structured records through this API rather than
    writing canonical tables directly. Raw provenance is retained separately
    from canonical entities so deduplication never destroys source evidence.
    """

    def __init__(self, path: str | Path, schema_path: str | Path | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = FULL")
        self.schema_path = Path(schema_path) if schema_path else self._default_schema_path()

    @staticmethod
    def _default_schema_path() -> Path:
        return Path(__file__).resolve().parents[3] / "database" / "schema.sql"

    @staticmethod
    def _json(value: Mapping[str, Any] | None) -> str:
        return json.dumps(value or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _now_us() -> int:
        return time.time_ns() // 1_000

    @staticmethod
    def normalize_identity(identity_type: str, value: str) -> str:
        kind = identity_type.lower().strip()
        value = value.strip()
        if kind == "email":
            return value.lower()
        if kind in {"phone", "imessage_handle"}:
            value = re.sub(r"[\s().-]+", "", value)
            if value.startswith("00"):
                value = "+" + value[2:]
        return value

    def initialize(self) -> None:
        schema = self.schema_path.read_text(encoding="utf-8")
        with self.conn:
            self.conn.executescript(schema)

    def close(self) -> None:
        self.conn.close()

    def begin_import(
        self,
        *,
        source_type: str,
        source_fingerprint: str,
        source_path: str | None = None,
        parser_version: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ImportRunResult:
        row = self.conn.execute(
            "SELECT id, status FROM import_run WHERE source_type=? AND source_fingerprint=?",
            (source_type, source_fingerprint),
        ).fetchone()
        if row is not None:
            if row["status"] == "completed":
                return ImportRunResult(int(row["id"]), True)
            with self.conn:
                self.conn.execute(
                    """UPDATE import_run
                       SET source_path=?, parser_version=?, started_at_utc_us=?,
                           finished_at_utc_us=NULL, status='running', metadata_json=?
                       WHERE id=?""",
                    (source_path, parser_version, self._now_us(), self._json(metadata), row["id"]),
                )
            return ImportRunResult(int(row["id"]), False)

        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO import_run(
                       source_type, source_path, source_fingerprint, parser_version,
                       started_at_utc_us, status, metadata_json
                   ) VALUES (?, ?, ?, ?, ?, 'running', ?)""",
                (source_type, source_path, source_fingerprint, parser_version,
                 self._now_us(), self._json(metadata)),
            )
        return ImportRunResult(int(cur.lastrowid), False)

    def finish_import(
        self,
        import_run_id: int,
        *,
        success: bool = True,
        statistics: Mapping[str, Any] | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """UPDATE import_run
                   SET finished_at_utc_us=?, status=?, statistics_json=? WHERE id=?""",
                (self._now_us(), "completed" if success else "failed",
                 self._json(statistics), import_run_id),
            )

    def get_or_create_participant(
        self,
        *,
        identity_type: str,
        identity_value: str,
        canonical_name: str | None = None,
        is_self: bool = False,
        metadata: Mapping[str, Any] | None = None,
    ) -> int:
        kind = identity_type.lower().strip()
        normalized = self.normalize_identity(kind, identity_value)
        row = self.conn.execute(
            "SELECT participant_id FROM participant_identity WHERE identity_type=? AND normalized_value=?",
            (kind, normalized),
        ).fetchone()
        if row is not None:
            return int(row["participant_id"])

        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO participant(canonical_name, is_self, metadata_json) VALUES (?, ?, ?)",
                (canonical_name, int(is_self), self._json(metadata)),
            )
            participant_id = int(cur.lastrowid)
            self.conn.execute(
                """INSERT INTO participant_identity(
                       participant_id, identity_type, normalized_value, original_value
                   ) VALUES (?, ?, ?, ?)""",
                (participant_id, kind, normalized, identity_value),
            )
        return participant_id

    def get_or_create_conversation(
        self,
        *,
        source_type: str,
        source_conversation_id: str,
        import_run_id: int | None = None,
        canonical_key: str | None = None,
        title: str | None = None,
        conversation_type: str = "unknown",
        service: str | None = None,
        participant_ids: Sequence[int] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> int:
        row = self.conn.execute(
            """SELECT conversation_id FROM conversation_source
               WHERE source_type=? AND source_conversation_id=?""",
            (source_type, source_conversation_id),
        ).fetchone()
        conversation_id = int(row["conversation_id"]) if row is not None else 0

        if not conversation_id and canonical_key is not None:
            row = self.conn.execute(
                "SELECT id FROM conversation WHERE canonical_key=?", (canonical_key,)
            ).fetchone()
            if row is not None:
                conversation_id = int(row["id"])

        with self.conn:
            if not conversation_id:
                cur = self.conn.execute(
                    """INSERT INTO conversation(
                           canonical_key, title, conversation_type, service, metadata_json
                       ) VALUES (?, ?, ?, ?, ?)""",
                    (canonical_key, title, conversation_type, service, self._json(metadata)),
                )
                conversation_id = int(cur.lastrowid)
            self.conn.execute(
                """INSERT OR IGNORE INTO conversation_source(
                       conversation_id, import_run_id, source_type, source_conversation_id
                   ) VALUES (?, ?, ?, ?)""",
                (conversation_id, import_run_id, source_type, source_conversation_id),
            )
            self.conn.executemany(
                """INSERT OR IGNORE INTO conversation_participant(conversation_id, participant_id)
                   VALUES (?, ?)""",
                [(conversation_id, pid) for pid in participant_ids],
            )
        return conversation_id

    @classmethod
    def source_hash(cls, record: MessageInput) -> str:
        payload = {
            "source_type": record.source_type,
            "source_message_id": record.source_message_id,
            "source_conversation_id": record.source_conversation_id,
            "source_row_id": record.source_row_id,
            "raw_timestamp": record.raw_timestamp,
            "raw_text": record.raw_text,
            "raw_payload": record.raw_payload or {},
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
        return sha256(raw).hexdigest()

    def insert_message(self, record: MessageInput) -> int:
        source_hash = self.source_hash(record)
        source_row = self.conn.execute(
            "SELECT message_id FROM message_source WHERE import_run_id=? AND source_hash=?",
            (record.import_run_id, source_hash),
        ).fetchone()
        if source_row is not None:
            return int(source_row["message_id"])

        message_id: int | None = None
        if record.canonical_guid is not None:
            row = self.conn.execute(
                "SELECT id FROM message WHERE service IS ? AND canonical_guid=?",
                (record.service, record.canonical_guid),
            ).fetchone()
            if row is not None:
                message_id = int(row["id"])

        with self.conn:
            if message_id is None:
                cur = self.conn.execute(
                    """INSERT INTO message(
                           conversation_id, sender_id, sent_at_utc_us, timezone_offset_min,
                           timestamp_precision, timestamp_quality, direction, message_type,
                           text, service, canonical_guid, created_import_id, metadata_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (record.conversation_id, record.sender_id, record.sent_at_utc_us,
                     record.timezone_offset_min, record.timestamp_precision,
                     record.timestamp_quality, record.direction, record.message_type,
                     record.text, record.service, record.canonical_guid,
                     record.import_run_id, self._json(record.metadata)),
                )
                message_id = int(cur.lastrowid)

            self.conn.execute(
                """INSERT INTO message_source(
                       message_id, import_run_id, source_type, source_message_id,
                       source_conversation_id, source_row_id, raw_timestamp, raw_text,
                       source_hash, raw_payload_json, metadata_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (message_id, record.import_run_id, record.source_type,
                 record.source_message_id, record.source_conversation_id,
                 record.source_row_id, record.raw_timestamp, record.raw_text,
                 source_hash, self._json(record.raw_payload), self._json(record.metadata)),
            )
        return message_id

    def add_attachment(
        self,
        *,
        message_id: int,
        import_run_id: int,
        sha256_value: str | None = None,
        mime_type: str | None = None,
        size_bytes: int | None = None,
        filename: str | None = None,
        storage_path: str | None = None,
        availability: str = "unknown",
        source_attachment_id: str | None = None,
        original_filename: str | None = None,
        original_path: str | None = None,
        raw_payload: Mapping[str, Any] | None = None,
    ) -> int:
        attachment_id: int | None = None
        if sha256_value:
            row = self.conn.execute(
                "SELECT id FROM attachment WHERE sha256=? LIMIT 1", (sha256_value,)
            ).fetchone()
            if row is not None:
                attachment_id = int(row["id"])

        with self.conn:
            if attachment_id is None:
                cur = self.conn.execute(
                    """INSERT INTO attachment(
                           sha256, mime_type, size_bytes, filename, storage_path, availability
                       ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (sha256_value, mime_type, size_bytes, filename, storage_path, availability),
                )
                attachment_id = int(cur.lastrowid)
            self.conn.execute(
                "INSERT OR IGNORE INTO message_attachment(message_id, attachment_id) VALUES (?, ?)",
                (message_id, attachment_id),
            )
            self.conn.execute(
                """INSERT INTO attachment_source(
                       attachment_id, import_run_id, source_attachment_id,
                       original_filename, original_path, raw_payload_json
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (attachment_id, import_run_id, source_attachment_id,
                 original_filename, original_path, self._json(raw_payload)),
            )
        return attachment_id

    def add_relation(
        self,
        source_message_id: int,
        target_message_id: int,
        relation_type: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT OR IGNORE INTO message_relation(
                       source_message_id, target_message_id, relation_type, metadata_json
                   ) VALUES (?, ?, ?, ?)""",
                (source_message_id, target_message_id, relation_type, self._json(metadata)),
            )

    def add_duplicate_candidate(
        self,
        message_id_a: int,
        message_id_b: int,
        *,
        reason: str,
        confidence: float | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        a, b = sorted((message_id_a, message_id_b))
        if a == b:
            return
        with self.conn:
            self.conn.execute(
                """INSERT OR IGNORE INTO duplicate_candidate(
                       message_id_a, message_id_b, reason, confidence, metadata_json
                   ) VALUES (?, ?, ?, ?, ?)""",
                (a, b, reason, confidence, self._json(metadata)),
            )

    def integrity_report(self) -> dict[str, Any]:
        integrity = self.conn.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = [dict(row) for row in self.conn.execute("PRAGMA foreign_key_check")]
        counts = {
            table: self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("conversation", "participant", "message", "message_source", "attachment")
        }
        return {"integrity": integrity, "foreign_key_errors": foreign_keys, "counts": counts}
