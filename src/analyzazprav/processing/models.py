from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class CanonicalMessage:
    """Read-only projection of one A2 canonical message."""

    id: int
    conversation_id: int
    sender_id: int | None
    timestamp_us: int | None
    text: str | None = None
    source_message_id: str | None = None
    source_order: int | None = None
    attachment_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MessageRelation:
    source_message_id: int
    target_message_id: int
    relation_type: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class A2Projection:
    messages: tuple[CanonicalMessage, ...]
    relations: tuple[MessageRelation, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class DuplicateCandidate:
    left_message_id: int
    right_message_id: int
    classification: str
    confidence: float
    method: str


@dataclass(frozen=True, slots=True)
class SenderRun:
    id: int
    conversation_id: int
    sender_id: int | None
    first_message_id: int
    last_message_id: int
    start_us: int | None
    end_us: int | None
    message_count: int
    char_count: int
    method: str = "deterministic_sender_run_v1"


@dataclass(frozen=True, slots=True)
class Session:
    id: int
    conversation_id: int
    first_message_id: int
    last_message_id: int
    start_us: int | None
    end_us: int | None
    message_count: int
    gap_threshold_us: int
    method: str = "temporal_gap_v1"


@dataclass(frozen=True, slots=True)
class Thread:
    id: int
    conversation_id: int
    session_id: int
    message_ids: tuple[int, ...]
    method: str
    confidence: float


@dataclass(frozen=True, slots=True)
class MessageFeatures:
    char_count: int
    word_count: int
    line_count: int
    emoji_count: int
    question_mark_count: int
    exclamation_mark_count: int
    uppercase_ratio: float
    has_question: bool
    has_url: bool
    has_attachment: bool
    seconds_since_previous_message: float | None
    seconds_since_previous_other_sender: float | None


@dataclass(frozen=True, slots=True)
class ProcessedMessage:
    message_id: int
    sequence_number: int
    text_clean: str | None
    sender_run_id: int
    session_id: int
    thread_id: int | None
    features: MessageFeatures


@dataclass(frozen=True, slots=True)
class ProcessingResult:
    messages: tuple[ProcessedMessage, ...]
    sender_runs: tuple[SenderRun, ...]
    sessions: tuple[Session, ...]
    threads: tuple[Thread, ...] = field(default_factory=tuple)
    duplicate_candidates: tuple[DuplicateCandidate, ...] = field(default_factory=tuple)
