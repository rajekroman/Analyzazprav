from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .database import CanonicalDatabase, MessageInput

SUPPORTED_A1_CONTRACT_VERSIONS = {"1"}


@dataclass(frozen=True)
class StagingIngestResult:
    import_run_id: int
    already_imported: bool
    messages: int = 0
    attachments: int = 0
    relations: int = 0


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


def ingest_a1_staging_bundle(
    db: CanonicalDatabase,
    staging_dir: str | Path,
) -> StagingIngestResult:
    """Ingest an A1 `manifest.json` + `messages.jsonl` staging bundle.

    A1 remains source extraction only. This function owns canonical participant,
    conversation, message, relation and attachment creation in A2.
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

    message_count = attachment_count = relation_count = 0
    pending_relations: list[tuple[int, str, str | None]] = []

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
            source_conversation_id = str(record.get("conversation_source_id") or "")
            source_record_key = str(record.get("source_record_key") or "")
            if not source_message_id or not source_conversation_id or not source_record_key:
                raise ValueError("A1 message requires source_message_id, conversation_source_id and source_record_key")

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
            conversation_id = db.get_or_create_conversation(
                source_type=source_type,
                source_conversation_id=source_conversation_id,
                import_run_id=run.id,
                service=None if service is None else str(service),
                participant_ids=participants,
            )

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
                    conversation_id=conversation_id,
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
                    source_conversation_id=source_conversation_id,
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

            for position, attachment in enumerate(attachments):
                if not isinstance(attachment, dict):
                    raise ValueError("A1 attachment record must be an object")
                source_path = attachment.get("source_path")
                sha256_value = attachment.get("sha256")
                filename = attachment.get("filename") or attachment.get("transfer_name")
                db.add_attachment(
                    message_id=message_id,
                    import_run_id=run.id,
                    sha256_value=None if sha256_value is None else str(sha256_value),
                    mime_type=None if attachment.get("mime_type") is None else str(attachment.get("mime_type")),
                    size_bytes=None
                    if attachment.get("total_bytes") is None
                    else int(attachment.get("total_bytes")),
                    filename=None if filename is None else str(filename),
                    availability=_attachment_availability(
                        None if source_path is None else str(source_path),
                        None if sha256_value is None else str(sha256_value),
                    ),
                    source_attachment_id=None
                    if attachment.get("source_attachment_id") is None
                    else str(attachment.get("source_attachment_id")),
                    original_filename=None
                    if attachment.get("transfer_name") is None
                    else str(attachment.get("transfer_name")),
                    original_path=None if source_path is None else str(source_path),
                    position=position,
                    raw_payload=attachment.get("raw_payload")
                    if isinstance(attachment.get("raw_payload"), dict)
                    else {},
                )
                attachment_count += 1

            reply_to_guid = record.get("reply_to_guid")
            if reply_to_guid:
                pending_relations.append(
                    (message_id, str(reply_to_guid), None if service is None else str(service))
                )

        expected_messages = counts.get("messages_seen")
        if expected_messages is not None and int(expected_messages) != message_count:
            raise ValueError(
                f"A1 manifest messages_seen={expected_messages} but JSONL contains {message_count} records"
            )

        for source_message_pk, reply_guid, service in pending_relations:
            target_message_pk = db.find_message_by_guid(reply_guid, service)
            if target_message_pk is None:
                continue
            db.add_relation(
                source_message_pk,
                target_message_pk,
                "reply_to",
                {"source": "a1.reply_to_guid", "target_guid": reply_guid},
            )
            relation_count += 1

        db.finish_import(
            run.id,
            statistics={
                "messages": message_count,
                "attachments": attachment_count,
                "relations": relation_count,
            },
        )
    except Exception:
        db.finish_import(
            run.id,
            success=False,
            statistics={
                "messages": message_count,
                "attachments": attachment_count,
                "relations": relation_count,
            },
        )
        raise

    return StagingIngestResult(
        import_run_id=run.id,
        already_imported=False,
        messages=message_count,
        attachments=attachment_count,
        relations=relation_count,
    )
