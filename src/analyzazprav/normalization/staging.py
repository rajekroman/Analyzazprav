from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Mapping

from .database import CanonicalDatabase, MessageInput
from .membership import (
    add_attachment_occurrence,
    get_or_create_source_conversation,
    link_message_conversation,
    message_source_pk,
)
from .relations import add_source_relation, resolve_source_relation

SUPPORTED_A1_CONTRACT_VERSIONS = {"1"}


class _AtomicImportConnection(sqlite3.Connection):
    """SQLite connection whose nested context managers can defer commits.

    Existing A2 write helpers use ``with db.conn:`` for safe standalone writes.
    During one A1 staging import those helper-level commits would otherwise make
    partially imported canonical rows visible when a later source record fails.
    The outer import transaction therefore temporarily defers context-manager
    commits while keeping explicit commit/rollback under the ingest coordinator.
    """

    defer_context_commits = False

    def __exit__(self, exc_type, exc, traceback):
        if self.defer_context_commits:
            return False
        return super().__exit__(exc_type, exc, traceback)


def _start_atomic_import(db: CanonicalDatabase) -> _AtomicImportConnection:
    """Reopen the canonical DB for one atomic import transaction.

    ``begin_import`` is deliberately committed before this function is called so
    a failed run remains auditable. All canonical/source writes after that point
    are rolled back together on failure, and only the run status is then updated
    to ``failed`` outside the rolled-back transaction.
    """

    if db.conn.in_transaction:
        raise RuntimeError("Cannot start A2 atomic import inside an existing transaction")

    db.conn.close()
    conn = sqlite3.connect(db.path, factory=_AtomicImportConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")
    conn.defer_context_commits = True
    conn.execute("BEGIN IMMEDIATE")
    db.conn = conn
    return conn


@dataclass(frozen=True)
class StagingIngestResult:
    import_run_id: int
    already_imported: bool
    messages: int = 0
    attachments: int = 0
    relations: int = 0
    source_relations: int = 0
    conversation_relations: int = 0


def iso_utc_to_unix_us(value: str | None) -> int | None:
    if not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    parsed = parsed.astimezone(timezone.utc)
    return int(parsed.timestamp() * 1_000_000)


def canonical_timestamp_precision(source_precision: str | None) -> str:
    # A2 physically stores integer UTC microseconds. A1 may know that the source
    # timestamp was nanosecond-based; that stronger source fact is preserved in
    # message_source metadata rather than overstating canonical storage precision.
    if source_precision == "nanosecond":
        return "microsecond"
    if source_precision in {"microsecond", "millisecond", "second", "minute"}:
        return source_precision
    return "unknown"


def _participant_identity(sender_handle: str) -> tuple[str, str]:
    handle = sender_handle.strip()
    if "@" in handle:
        return "email", handle
    compact = "".join(ch for ch in handle if ch not in " +()-.")
    if compact.isdigit() and compact:
        return "phone", handle
    return "imessage_handle", handle


def _source_fingerprint(manifest: Mapping[str, Any]) -> str:
    """Fingerprint one concrete A1 ingest representation, not the raw source bytes."""
    source = manifest.get("source") or {}
    parser = manifest.get("parser") or {}
    payload = {
        "contract_version": str(manifest.get("contract_version", "")),
        "source_type": source.get("type"),
        "source_sha256": source.get("sha256"),
        "parser_name": parser.get("name"),
        "parser_version": parser.get("version"),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(raw).hexdigest()


def _load_json_lines(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid A1 JSONL at {path}:{line_number}: {exc}") from exc
            if not isinstance(item, dict):
                raise ValueError(f"A1 record at {path}:{line_number} is not an object")
            yield item


def _attachment_availability(source_path: str | None, sha256_value: str | None) -> str:
    if source_path:
        path = Path(source_path).expanduser()
        return "external" if path.exists() else "missing"
    if sha256_value:
        return "external"
    return "unknown"


def _conversation_refs(
    record: Mapping[str, Any],
    *,
    source_type: str,
) -> list[tuple[str, str | None, dict[str, Any]]]:
    """Return ordered source conversation relations for one physical source message.

    A1 contract v1 uses ``conversation_source_id``. The lossless extension accepts
    ``conversation_sources`` so one physical source message can carry multiple
    chat relations without duplicating the source message record.
    """

    raw_refs = record.get("conversation_sources")
    refs: list[tuple[str, str | None, dict[str, Any]]] = []

    if raw_refs is not None:
        if not isinstance(raw_refs, list):
            raise ValueError("A1 conversation_sources must be an array when present")
        for index, raw_ref in enumerate(raw_refs):
            if isinstance(raw_ref, str):
                source_conversation_id = raw_ref.strip()
                canonical_key = None
                metadata: dict[str, Any] = {"source_relation_index": index}
            elif isinstance(raw_ref, dict):
                metadata = dict(raw_ref)
                metadata["source_relation_index"] = index
                chat_guid = raw_ref.get("chat_guid")
                raw_rowid = raw_ref.get("raw_chat_rowid")
                value = (
                    raw_ref.get("source_conversation_key")
                    or raw_ref.get("conversation_source_id")
                    or raw_ref.get("source_conversation_id")
                )
                if value is None and chat_guid is not None:
                    value = f"guid:{chat_guid}"
                if value is None and raw_rowid is not None:
                    value = f"rowid:{raw_rowid}"
                source_conversation_id = "" if value is None else str(value).strip()
                explicit_canonical = raw_ref.get("canonical_key")
                canonical_key = (
                    str(explicit_canonical)
                    if explicit_canonical is not None
                    else f"{source_type}:chat-guid:{chat_guid}"
                    if chat_guid is not None
                    else None
                )
            else:
                raise ValueError("A1 conversation_sources entries must be strings or objects")

            if not source_conversation_id:
                raise ValueError("A1 conversation source relation has no usable identity")
            refs.append((source_conversation_id, canonical_key, metadata))
    else:
        legacy = str(record.get("conversation_source_id") or "").strip()
        if legacy:
            refs.append(
                (
                    legacy,
                    None,
                    {"contract_field": "conversation_source_id", "source_relation_index": 0},
                )
            )

    if not refs:
        raise ValueError(
            "A1 message requires conversation_source_id or at least one conversation_sources relation"
        )

    deduplicated: list[tuple[str, str | None, dict[str, Any]]] = []
    seen: set[tuple[str, str | None]] = set()
    for ref in refs:
        identity = (ref[0], ref[1])
        if identity in seen:
            continue
        seen.add(identity)
        deduplicated.append(ref)
    return deduplicated


def ingest_a1_staging_bundle(
    db: CanonicalDatabase,
    staging_dir: str | Path,
) -> StagingIngestResult:
    """Ingest an A1 ``manifest.json`` + ``messages.jsonl`` staging bundle.

    A1 remains source extraction only. A2 owns canonical entities and preserves
    every source message occurrence, every source conversation relation, every
    explicit source message relation and every attachment occurrence without
    relying on database-local ROWIDs as global IDs.

    The audit ``import_run`` is committed first. All canonical/source writes for
    a non-idempotent run are then one atomic SQLite transaction: either the whole
    representation is committed and the run becomes ``completed``, or all of
    those writes are rolled back and the run alone is marked ``failed``.
    """

    root = Path(staging_dir)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("A1 manifest must be a JSON object")

    contract_version = str(manifest.get("contract_version", ""))
    if contract_version not in SUPPORTED_A1_CONTRACT_VERSIONS:
        raise ValueError(f"Unsupported A1 contract_version: {contract_version!r}")

    counts = manifest.get("counts") or {}
    if int(counts.get("errors", 0) or 0) != 0:
        raise ValueError("A1 staging manifest reports extraction errors; refusing partial canonical ingest")

    source = manifest.get("source") or {}
    parser = manifest.get("parser") or {}
    outputs = manifest.get("outputs") or {}
    source_type = str(source.get("type") or "")
    source_sha256 = str(source.get("sha256") or "")
    if not source_type or not source_sha256:
        raise ValueError("A1 manifest source.type and source.sha256 are required")

    messages_name = str(outputs.get("messages") or "messages.jsonl")
    messages_path = root / messages_name
    if not messages_path.is_file():
        raise FileNotFoundError(messages_path)

    run = db.begin_import(
        source_type=source_type,
        source_fingerprint=_source_fingerprint(manifest),
        source_sha256=source_sha256,
        source_path=str(root),
        parser_version=str(parser.get("version") or "") or None,
        metadata={
            "a1_contract_version": contract_version,
            "parser": parser,
            "source": source,
        },
    )
    if run.already_imported:
        return StagingIngestResult(import_run_id=run.id, already_imported=True)

    atomic_conn = _start_atomic_import(db)
    message_count = attachment_count = relation_count = 0
    source_relation_count = conversation_relation_count = 0
    pending_relations: list[tuple[int, int, str, str | None]] = []

    try:
        for record in _load_json_lines(messages_path):
            if str(record.get("contract_version", "")) != contract_version:
                raise ValueError("A1 record contract_version does not match manifest")
            if record.get("record_type") != "message":
                raise ValueError(f"Unsupported A1 record_type: {record.get('record_type')!r}")
            if record.get("source_type") != source_type:
                raise ValueError("A1 record source_type does not match manifest")
            if record.get("source_sha256") != source_sha256:
                raise ValueError("A1 record source_sha256 does not match manifest")

            source_message_id = str(record.get("source_message_id") or "")
            source_record_key = str(record.get("source_record_key") or "")
            if not source_message_id or not source_record_key:
                raise ValueError("A1 message requires source_message_id and source_record_key")

            is_from_me = bool(record.get("is_from_me"))
            sender_handle = record.get("sender_handle")
            if is_from_me:
                sender_id = db.get_or_create_participant(
                    identity_type="self",
                    identity_value="local",
                    canonical_name="Me",
                    is_self=True,
                )
            elif sender_handle:
                identity_type, identity_value = _participant_identity(str(sender_handle))
                sender_id = db.get_or_create_participant(
                    identity_type=identity_type,
                    identity_value=identity_value,
                )
            else:
                sender_id = None

            service = record.get("service")
            participants = [sender_id] if sender_id is not None else []
            conversation_refs = _conversation_refs(record, source_type=source_type)
            resolved_conversations: list[tuple[int, int, str, dict[str, Any]]] = []
            for source_conversation_id, canonical_key, relation_metadata in conversation_refs:
                conversation_id, conversation_source_pk = get_or_create_source_conversation(
                    db,
                    import_run_id=run.id,
                    source_type=source_type,
                    source_sha256=source_sha256,
                    source_conversation_id=source_conversation_id,
                    canonical_key=canonical_key,
                    service=None if service is None else str(service),
                    participant_ids=participants,
                    metadata=relation_metadata,
                )
                resolved_conversations.append(
                    (
                        conversation_id,
                        conversation_source_pk,
                        source_conversation_id,
                        relation_metadata,
                    )
                )

            primary_conversation_id = resolved_conversations[0][0]
            primary_source_conversation_id = resolved_conversations[0][2]

            timestamp_utc = record.get("timestamp_utc")
            source_precision = record.get("timestamp_precision")
            sent_at_utc_us = iso_utc_to_unix_us(
                None if timestamp_utc is None else str(timestamp_utc)
            )
            raw_payload = record.get("raw_payload")
            raw_payload = raw_payload if isinstance(raw_payload, dict) else {}
            metadata = record.get("metadata")
            metadata = dict(metadata) if isinstance(metadata, dict) else {}
            metadata.update(
                {
                    "a1_text_source": record.get("text_source"),
                    "a1_source_timestamp_precision": source_precision,
                    "a1_source_sha256": source_sha256,
                }
            )

            attachments = record.get("attachments") or []
            if not isinstance(attachments, list):
                raise ValueError("A1 attachments must be an array")
            text = record.get("text")
            message_type = "attachment" if text is None and attachments else "text"
            canonical_guid = record.get("source_guid")
            message_id = db.insert_message(
                MessageInput(
                    import_run_id=run.id,
                    source_type=source_type,
                    conversation_id=primary_conversation_id,
                    sender_id=sender_id,
                    sent_at_utc_us=sent_at_utc_us,
                    direction="outgoing" if is_from_me else "incoming",
                    message_type=message_type,
                    text=None if text is None else str(text),
                    service=None if service is None else str(service),
                    canonical_guid=None if canonical_guid is None else str(canonical_guid),
                    timestamp_precision=canonical_timestamp_precision(
                        None if source_precision is None else str(source_precision)
                    ),
                    timestamp_quality="converted" if sent_at_utc_us is not None else "unknown",
                    source_message_id=source_message_id,
                    source_conversation_id=primary_source_conversation_id,
                    source_row_id=source_message_id if source_message_id.isdigit() else None,
                    source_record_key=source_record_key,
                    source_contract_version=contract_version,
                    raw_timestamp=None
                    if record.get("timestamp_raw") is None
                    else str(record.get("timestamp_raw")),
                    raw_text=None if record.get("raw_text") is None else str(record.get("raw_text")),
                    raw_payload=raw_payload,
                    metadata=metadata,
                )
            )
            message_count += 1

            source_pk = message_source_pk(
                db,
                import_run_id=run.id,
                source_record_key=source_record_key,
            )
            for position, (
                conversation_id,
                conversation_source_pk,
                _,
                relation_metadata,
            ) in enumerate(resolved_conversations):
                link_message_conversation(
                    db,
                    message_id=message_id,
                    conversation_id=conversation_id,
                    message_source_id=source_pk,
                    conversation_source_id=conversation_source_pk,
                    position=position,
                    prefer_primary=position == 0,
                    metadata=relation_metadata,
                )
                conversation_relation_count += 1

            for position, attachment in enumerate(attachments):
                if not isinstance(attachment, dict):
                    raise ValueError("A1 attachment record must be an object")
                source_path = attachment.get("source_path")
                sha256_value = attachment.get("sha256")
                filename = attachment.get("filename") or attachment.get("transfer_name")
                add_attachment_occurrence(
                    db,
                    message_id=message_id,
                    import_run_id=run.id,
                    source_record_key=source_record_key,
                    source_attachment_id=None
                    if attachment.get("source_attachment_id") is None
                    else str(attachment.get("source_attachment_id")),
                    position=position,
                    sha256_value=None if sha256_value is None else str(sha256_value),
                    mime_type=None
                    if attachment.get("mime_type") is None
                    else str(attachment.get("mime_type")),
                    size_bytes=None
                    if attachment.get("total_bytes") is None
                    else int(attachment.get("total_bytes")),
                    filename=None if filename is None else str(filename),
                    availability=_attachment_availability(
                        None if source_path is None else str(source_path),
                        None if sha256_value is None else str(sha256_value),
                    ),
                    original_filename=None
                    if attachment.get("transfer_name") is None
                    else str(attachment.get("transfer_name")),
                    original_path=None if source_path is None else str(source_path),
                    raw_payload=attachment.get("raw_payload")
                    if isinstance(attachment.get("raw_payload"), dict)
                    else {},
                )
                attachment_count += 1

            reply_to_guid = record.get("reply_to_guid")
            if reply_to_guid:
                reply_guid = str(reply_to_guid)
                relation_service = None if service is None else str(service)
                source_relation_id = add_source_relation(
                    db,
                    message_source_id=source_pk,
                    import_run_id=run.id,
                    source_record_key=source_record_key,
                    relation_type="reply_to",
                    target_source_guid=reply_guid,
                    target_service=relation_service,
                    metadata={
                        "source": "a1.reply_to_guid",
                        "target_guid": reply_guid,
                    },
                )
                source_relation_count += 1
                pending_relations.append(
                    (source_relation_id, message_id, reply_guid, relation_service)
                )

        expected_messages = counts.get("messages_seen")
        if expected_messages is not None and int(expected_messages) != message_count:
            raise ValueError(
                f"A1 manifest messages_seen={expected_messages} but JSONL contains {message_count} records"
            )
        expected_attachments = counts.get("attachments_seen")
        if expected_attachments is not None and int(expected_attachments) != attachment_count:
            raise ValueError(
                f"A1 manifest attachments_seen={expected_attachments} but JSONL contains {attachment_count} attachment records"
            )

        for source_relation_id, source_message_pk, reply_guid, service in pending_relations:
            target_message_pk = db.find_message_by_guid(reply_guid, service)
            if target_message_pk is None:
                continue
            resolve_source_relation(
                db,
                source_relation_id=source_relation_id,
                source_message_id=source_message_pk,
                target_message_id=target_message_pk,
                canonical_metadata={
                    "source": "a1.reply_to_guid",
                    "target_guid": reply_guid,
                },
            )
            relation_count += 1

        db.finish_import(
            run.id,
            statistics={
                "messages": message_count,
                "attachments": attachment_count,
                "relations": relation_count,
                "source_relations": source_relation_count,
                "conversation_relations": conversation_relation_count,
            },
        )
        atomic_conn.commit()
        atomic_conn.defer_context_commits = False
    except Exception:
        if atomic_conn.in_transaction:
            atomic_conn.rollback()
        atomic_conn.defer_context_commits = False
        db.finish_import(
            run.id,
            success=False,
            statistics={
                "messages": message_count,
                "attachments": attachment_count,
                "relations": relation_count,
                "source_relations": source_relation_count,
                "conversation_relations": conversation_relation_count,
            },
        )
        raise

    return StagingIngestResult(
        import_run_id=run.id,
        already_imported=False,
        messages=message_count,
        attachments=attachment_count,
        relations=relation_count,
        source_relations=source_relation_count,
        conversation_relations=conversation_relation_count,
    )
