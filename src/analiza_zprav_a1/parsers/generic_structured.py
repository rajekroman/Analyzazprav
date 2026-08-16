from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from ..csv_mapping import CSVMappingProfile
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


def _lookup(
    mapping: Mapping[str, Any],
    field: str,
    explicit_columns: Mapping[str, str] | None = None,
) -> tuple[Any, str | None]:
    if explicit_columns is not None:
        column = explicit_columns.get(field)
        if column is None:
            return None, None
        return mapping.get(column), column

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


def record_from_mapping(
    mapping: dict[str, Any],
    *,
    ordinal: int,
    source_name: str,
    explicit_columns: Mapping[str, str] | None = None,
) -> MessageRecord:
    source_id_raw, source_id_col = _lookup(mapping, "id", explicit_columns)
    guid_raw, guid_col = _lookup(mapping, "guid", explicit_columns)
    conversation_raw, conversation_col = _lookup(mapping, "conversation", explicit_columns)
    sender_raw, sender_col = _lookup(mapping, "sender", explicit_columns)
    timestamp_raw, timestamp_col = _lookup(mapping, "timestamp", explicit_columns)
    text_raw, text_col = _lookup(mapping, "text", explicit_columns)
    service_raw, service_col = _lookup(mapping, "service", explicit_columns)
    direction_raw, direction_col = _lookup(mapping, "direction", explicit_columns)
    attachment_raw, attachment_col = _lookup(mapping, "attachment", explicit_columns)

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


def _reject_duplicate_headers(fieldnames: list[str]) -> None:
    if len(fieldnames) != len(set(fieldnames)):
        duplicates = sorted({name for name in fieldnames if fieldnames.count(name) > 1})
        raise ValueError(f"CSV contains duplicate header names: {duplicates!r}")


class GenericCSVParser:
    def __init__(self, path: Path, mapping_profile: CSVMappingProfile | None = None):
        self.path = path
        self.mapping_profile = mapping_profile

    def _iter_profiled(self, stream: Any) -> Iterator[MessageRecord]:
        assert self.mapping_profile is not None
        profile = self.mapping_profile
        reader = csv.reader(stream, delimiter=profile.delimiter)

        if profile.has_header:
            try:
                header = next(reader)
            except StopIteration as exc:
                raise ValueError("CSV mapping profile declares a header but the source is empty") from exc
            _reject_duplicate_headers(header)
            source_columns: dict[str, str] = {}
            for field, selector in profile.fields.items():
                assert isinstance(selector, str)
                if selector not in header:
                    raise ValueError(
                        f"CSV mapping profile field {field!r} references missing header {selector!r}"
                    )
                source_columns[field] = selector

            for row_number, values in enumerate(reader, start=2):
                if len(values) != len(header):
                    raise ValueError(
                        f"CSV row {row_number} has {len(values)} columns; header defines {len(header)}"
                    )
                row = {header[index]: value for index, value in enumerate(values)}
                yield record_from_mapping(
                    row,
                    ordinal=row_number,
                    source_name=self.path.stem,
                    explicit_columns=source_columns,
                )
            return

        max_index = max(int(selector) for selector in profile.fields.values())
        source_columns = {
            field: f"column:{int(selector)}" for field, selector in profile.fields.items()
        }
        for row_number, values in enumerate(reader, start=1):
            if len(values) <= max_index:
                raise ValueError(
                    f"CSV row {row_number} has {len(values)} columns; mapping requires index {max_index}"
                )
            row = {f"column:{index}": value for index, value in enumerate(values)}
            yield record_from_mapping(
                row,
                ordinal=row_number,
                source_name=self.path.stem,
                explicit_columns=source_columns,
            )

    def iter_messages(self) -> Iterator[MessageRecord]:
        with self.path.open("r", encoding="utf-8-sig", newline="") as stream:
            if self.mapping_profile is not None:
                yield from self._iter_profiled(stream)
                return

            sample = stream.read(8192)
            stream.seek(0)
            reader = csv.DictReader(stream, dialect=_sniff(sample))
            if not reader.fieldnames:
                raise ValueError("CSV has no header row")
            fieldnames = [str(name) for name in reader.fieldnames]
            _reject_duplicate_headers(fieldnames)
            normalized = {_norm(name) for name in fieldnames if name}
            if not normalized.intersection(ALIASES["text"] | ALIASES["timestamp"] | ALIASES["sender"]):
                raise ValueError("CSV headers do not contain a supported message field")
            for row_number, source_row in enumerate(reader, start=2):
                if None in source_row:
                    raise ValueError(
                        f"CSV row {row_number} contains more fields than the header; refusing silent truncation"
                    )
                row = {str(k): "" if v is None else v for k, v in source_row.items()}
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
