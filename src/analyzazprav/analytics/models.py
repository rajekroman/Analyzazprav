from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AnalyticMessage:
    """A4 read model built from A2 analysis_messages + A3 processed_message."""

    message_id: int
    conversation_id: int
    participant_id: int | None
    timestamp_us: int | None
    text_clean: str
    session_id: int
    sequence_number: int
    word_count: int
    character_count: int
    question_mark_count: int
    exclamation_mark_count: int
    has_attachment: bool = False


@dataclass(frozen=True, slots=True)
class Turn:
    turn_id: int
    conversation_id: int
    session_id: int
    participant_id: int | None
    start_us: int | None
    end_us: int | None
    message_ids: tuple[int, ...]
    message_count: int
    word_count: int
    character_count: int
    question_mark_count: int
    exclamation_mark_count: int
    text: str


@dataclass(frozen=True, slots=True)
class ResponseLatency:
    conversation_id: int
    session_id: int
    from_participant_id: int
    responder_id: int
    previous_turn_id: int
    response_turn_id: int
    latency_seconds: float


@dataclass(frozen=True, slots=True)
class ConflictCandidate:
    conversation_id: int
    session_id: int
    score: float
    start_us: int | None
    end_us: int | None
    factors: dict[str, float]
    source_message_ids: tuple[int, ...]


@dataclass(slots=True)
class ConversationAnalytics:
    conversation_id: int
    source_message_count: int
    known_sender_message_count: int
    unknown_sender_message_count: int
    turn_count: int
    session_count: int
    participant_metrics: dict[int, dict[str, Any]] = field(default_factory=dict)
    reciprocity: dict[str, float | None] = field(default_factory=dict)
    latency_samples: list[ResponseLatency] = field(default_factory=list)
    conflicts: list[ConflictCandidate] = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
