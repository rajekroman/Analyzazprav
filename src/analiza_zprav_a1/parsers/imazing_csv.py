from __future__ import annotations

import csv
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from ..models import AttachmentRecord, MessageRecord


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.strip().lower())


ALIASES = {
    "conversation": {
        "chatsession", "chatsessionname", "conversation", "conversationname", "chat", "chatname", "session"
    },
    "sender": {"sender", "sendername", "from", "fromname", "participant", "contact"},
    "date": {"sentdate", "date", "datetime", "sentdatetime", "timestamp", "time"},
    "text": {"message", "messagetext", "text", "body", "content"},
    "service": {"service", "servicetype", "type"},
    "direction": {"direction", "sentreceived", "incomingoutgoing", "fromme", "sentbyme"},
    "attachment": {
        "attachment", "attachments", "attachmentfilename", "attachmentfile", "filename", "file"
    },
    "attachment_type": {"attachmenttype", "mimetype", "mediatype"},
}


def _find(row: dict[str, str], field: str) -> tuple[str | None, str | None]:
    aliases = ALIASES[field]
    for key, value in row.items():
        if _norm(key) in aliases:
            return value if value != "" else None, key
    return None, None


def _aware_iso(raw: str | None) -> tuple[str | None, str | None]:
    if not raw:
        return None, None
    value = raw.strip()
    candidates = [value]
    if value.endswith("Z"):
        candidates.insert(0, value[:-1] + "+00:00")
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


def _direction(raw: str | None) -> bool | None:
    if raw is None:
        return None
    value = _norm(raw)
    if value in {"outgoing", "sent", "me", "mine", "true", "yes", "1"}:
        return True
    if value in {"incoming", "received", "false", "no", "0"}:
        return False
    return None


def _sniff(sample: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        return csv.excel


class IMazingCSVParser:
    def __init__(self, csv_path: Path):
        self.csv_path = csv_path

    def iter_messages(self) -> Iterator[MessageRecord]:
        with self.csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
            sample = stream.read(8192)
            stream.seek(0)
            dialect = _sniff(sample)
            reader = csv.DictReader(stream, dialect=dialect)
            if not reader.fieldnames:
                raise ValueError("iMazing CSV has no header row")

            normalized_headers = {_norm(name) for name in reader.fieldnames if name}
            if not normalized_headers.intersection(ALIASES["text"] | ALIASES["date"] | ALIASES["sender"]):
                raise ValueError("CSV does not look like an iMazing Messages export")

            for row_index, source_row in enumerate(reader, start=2):
                row = {str(k): "" if v is None else str(v) for k, v in source_row.items() if k is not None}
                conversation, conversation_column = _find(row, "conversation")
                sender, sender_column = _find(row, "sender")
                date_raw, date_column = _find(row, "date")
                text, text_column = _find(row, "text")
                service, service_column = _find(row, "service")
                direction, direction_column = _find(row, "direction")
                attachment_path, attachment_column = _find(row, "attachment")
                attachment_type, attachment_type_column = _find(row, "attachment_type")

                timestamp_utc, timestamp_precision = _aware_iso(date_raw)
                attachments: list[AttachmentRecord] = []
                if attachment_path:
                    attachments.append(
                        AttachmentRecord(
                            source_attachment_id=f"row:{row_index}:attachment:1",
                            filename=Path(attachment_path).name,
                            mime_type=attachment_type,
                            transfer_name=Path(attachment_path).name,
                            total_bytes=None,
                            source_path=attachment_path,
                            raw_payload={
                                "attachment_column": attachment_column,
                                "attachment_type_column": attachment_type_column,
                            },
                        )
                    )

                metadata = {
                    "row_number": row_index,
                    "column_map": {
                        "conversation": conversation_column,
                        "sender": sender_column,
                        "date": date_column,
                        "text": text_column,
                        "service": service_column,
                        "direction": direction_column,
                    },
                }

                yield MessageRecord(
                    source_message_id=f"row:{row_index}",
                    source_guid=None,
                    conversation_source_id=conversation or self.csv_path.stem,
                    timestamp_raw=date_raw,
                    timestamp_utc=timestamp_utc,
                    timestamp_precision=timestamp_precision,
                    sender_handle=sender,
                    is_from_me=_direction(direction),
                    text=text,
                    raw_text=text,
                    text_source=text_column,
                    service=service,
                    reply_to_guid=None,
                    attachments=attachments,
                    raw_payload=row,
                    metadata=metadata,
                )
