from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AttachmentRecord:
    source_attachment_id: str
    filename: str | None
    mime_type: str | None
    transfer_name: str | None
    total_bytes: int | None
    source_path: str | None
    sha256: str | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MessageRecord:
    source_message_id: str
    source_guid: str | None
    conversation_source_id: str
    timestamp_raw: int | float | None
    timestamp_utc: str | None
    timestamp_precision: str | None
    sender_handle: str | None
    is_from_me: bool
    text: str | None
    raw_text: str | None
    text_source: str | None
    service: str | None
    reply_to_guid: str | None = None
    attachments: list[AttachmentRecord] = field(default_factory=list)
    raw_payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
