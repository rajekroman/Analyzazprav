from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..models import AttachmentRecord, MessageRecord


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


ALIASES = {
    "id": {"id", "messageid", "msgid", "recordid"},
    "guid": {"guid", "messageguid", "uuid"},
    "conversation": {"conversation", "conversationid", "chat", "chatid", "thread", "threadid", "session"},
    "sender": {"sender", "senderid", "from", "fromid", "author", "participant", "contact"},
    "timestamp": {"timestamp", "datetime", "date", "sentdate", "createdat", "time"},
    "text": {"text", "message", "messagetext", "body", "content"},
    "service": {"service", "servicetype", "channel", "platform"},
    "direction": {"direction", "sentreceived", "incomingoutgoing", "fromme", "isfromme", "sentbyme"},
    "attachment": {"attachment", "attachments", "attachmentpath", "attachmentfile", "filename", "file"},
}


def _lookup(mapping: dict[str, Any], field: str) -> tuple[Any, str | None]:
    aliases = ALIASES[field]
    for key, value in mapping.items():
        if _norm(str(key)) in aliases:
            return value, str(key)
    return None, None


def _direction(raw: Any) -> bool | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    value = _norm(str(raw))
    if value in {"outgoing", "sent", "me", "mine", "true", "yes", "1"}:
        return True
    if value in {"incoming", "received", "false", "no", "0"}:
        return False
    return None


def _timestamp(raw: Any) -> tuple[str | None, str | None]:
    if raw is None or raw == "":
        return None, None
    if isinstance(raw, (int, float)):
        return None, "numeric_unknown"
    value = str(raw).strip()
    candidates = [value[:-1] + "+00:00", value] if value.endswith("Z") else [value]
    for candidate in candidates:
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if dt.tzinfo is None:
            return None, "local_text"
        utc = dt.astimezone(timezone.utc)
        precision = "microsecond" if dt.microsecond else "second"
        return utc.isoformat().replace("+00:00", "Z"), precision
    return None, "text"


def _attachment_records(value: Any, record_id: str) -> list[AttachmentRecord]:
    if value in (None, "", []):
        return []
    items = value if isinstance(value, list) else [value]
    out: list[AttachmentRecord] = []
    for index, item in enumerate(items, start=1):
        if isinstance(item, dict):
            path = item.get("path") or item.get("filename") or item.get("file") or item.get("name")
            mime_type = item.get("mime_type") or item.get("mimetype") or item.get("mime")
            raw_size = item.get("total_bytes") or item.get("size") or item.get("bytes")
            try:
                total_bytes = int(raw_size) if raw_size is not None else None
            except (TypeError, ValueError):
                total_bytes = None
            raw_payload = item
        else:
            path = str(item)
            mime_type = None
            total_bytes = None
            raw_payload = {"value": item}
        filename = Path(str(path)).name if path else None
        out.append(
            AttachmentRecord(
                source_attachment_id=f"{record_id}:attachment:{index}",
                filename=filename,
                mime_type=str(mime_type) if mime_type else None,
                transfer_name=filename,
                total_bytes=total_bytes,
                source_path=str(path) if path else None,
                raw_payload=raw_payload,
            )
        )
    return out


def record_from_mapping(mapping: dict[str, Any], *, ordinal: int, source_name: str) -> MessageRecord:
    source_id_raw, source_id_col = _lookup(mapping, "id")
    guid_raw, guid_col = _lookup(mapping, "guid")
    conversation_raw, conversation_col = _lookup(mapping, "conversation")
    sender_raw, sender_col = _lookup(mapping, "sender")
    timestamp_raw, timestamp_col = _lookup(mapping, "timestamp")
    text_raw, text_col = _lookup(mapping, "text")
    service_raw, service_col = _lookup(mapping, "service")
    direction_raw, direction_col = _lookup(mapping, "direction")
    attachment_raw, attachment_col = _lookup(mapping, "attachment")

    source_id = str(source_id_raw) if source_id_raw not in (None, "") else f"item:{ordinal}"
    source_guid = str(guid_raw) if guid_raw not in (None, "") else None
    conversation = str(conversation_raw) if conversation_raw not in (None, "") else source_name
    text = str(text_raw) if text_raw is not None else None
    timestamp_utc, timestamp_precision = _timestamp(timestamp_raw)

    return MessageRecord(
        source_message_id=source_id,
        source_guid=source_guid,
        conversation_source_id=conversation,
        timestamp_raw=timestamp_raw,
        timestamp_utc=timestamp_utc,
        timestamp_precision=timestamp_precision,
        sender_handle=str(sender_raw) if sender_raw not in (None, "") else None,
        is_from_me=_direction(direction_raw),
        text=text,
        raw_text=text,
        text_source=text_col,
        service=str(service_raw) if service_raw not in (None, "") else None,
        attachments=_attachment_records(attachment_raw, source_id),
        raw_payload=mapping,
        metadata={
            "ordinal": ordinal,
            "column_map": {
                "id": source_id_col,
                "guid": guid_col,
                "conversation": conversation_col,
                "sender": sender_col,
                "timestamp": timestamp_col,
                "text": text_col,
                "service": service_col,
                "direction": direction_col,
                "attachment": attachment_col,
            },
        },
    )


def _sniff(sample: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        return csv.excel


class GenericCSVParser:
    def __init__(self, path: Path):
        self.path = path

    def iter_messages(self) -> Iterator[MessageRecord]:
        with self.path.open("r", encoding="utf-8-sig", newline="") as stream:
            sample = stream.read(8192)
            stream.seek(0)
            reader = csv.DictReader(stream, dialect=_sniff(sample))
            if not reader.fieldnames:
                raise ValueError("CSV has no header row")
            normalized = {_norm(name) for name in reader.fieldnames if name}
            if not normalized.intersection(ALIASES["text"] | ALIASES["timestamp"] | ALIASES["sender"]):
                raise ValueError("CSV headers do not contain a supported message field")
            for row_number, source_row in enumerate(reader, start=2):
                row = {str(k): "" if v is None else v for k, v in source_row.items() if k is not None}
                yield record_from_mapping(row, ordinal=row_number, source_name=self.path.stem)


class GenericJSONParser:
    def __init__(self, path: Path):
        self.path = path

    def _items(self) -> Iterator[dict[str, Any]]:
        if self.path.suffix.lower() == ".jsonl":
            with self.path.open("r", encoding="utf-8-sig") as stream:
                for line_number, line in enumerate(stream, start=1):
                    if not line.strip():
                        continue
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError(f"JSONL line {line_number} is not an object")
                    yield value
            return

        value = json.loads(self.path.read_text(encoding="utf-8-sig"))
        if isinstance(value, list):
            items = value
        elif isinstance(value, dict) and isinstance(value.get("messages"), list):
            items = value["messages"]
        elif isinstance(value, dict):
            items = [value]
        else:
            raise ValueError("JSON source must be an object, a list of objects, or contain a messages list")
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"JSON item {index} is not an object")
            yield item

    def iter_messages(self) -> Iterator[MessageRecord]:
        for ordinal, item in enumerate(self._items(), start=1):
            yield record_from_mapping(item, ordinal=ordinal, source_name=self.path.stem)
