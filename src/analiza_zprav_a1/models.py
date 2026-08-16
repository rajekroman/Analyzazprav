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
    resolved_path: str | None = None
    resolution_status: str | None = None
    actual_bytes: int | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ConversationSourceRecord:
    """One source-level message↔conversation relation.

    `source_conversation_key` is stable inside an immutable source snapshot. A
    real Apple chat GUID is preferred; a database-local ROWID is retained only
    as an explicitly labelled fallback/provenance value.
    """

    source_conversation_key: str
    raw_chat_rowid: int | None = None
    chat_guid: str | None = None
    service: str | None = None
    participant_handles: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MessageRecord:
    source_message_id: str
    source_guid: str | None
    conversation_source_id: str
    timestamp_raw: int | float | str | None
    timestamp_utc: str | None
    timestamp_precision: str | None
    sender_handle: str | None
    is_from_me: bool | None
    text: str | None
    raw_text: str | None
    text_source: str | None
    service: str | None
    conversation_sources: list[ConversationSourceRecord] = field(default_factory=list)
    conversation_participant_handles: list[str] = field(default_factory=list)
    conversation_metadata: dict[str, Any] = field(default_factory=dict)
    reply_to_guid: str | None = None
    attachments: list[AttachmentRecord] = field(default_factory=list)
    raw_payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
