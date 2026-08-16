from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class Message:
    message_id: str
    conversation_id: str
    participant_id: str
    timestamp: datetime
    text: str = ""
    message_type: str = "text"
    is_reaction: bool = False


@dataclass(frozen=True, slots=True)
class Turn:
    turn_id: str
    conversation_id: str
    participant_id: str
    start_at: datetime
    end_at: datetime
    message_ids: tuple[str, ...]
    message_count: int
    word_count: int
    character_count: int
    text: str


@dataclass(frozen=True, slots=True)
class Session:
    session_id: str
    conversation_id: str
    start_at: datetime
    end_at: datetime
    initiator_id: str
    turn_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LatencySample:
    session_id: str
    from_participant_id: str
    responder_id: str
    previous_turn_id: str
    response_turn_id: str
    latency_seconds: float


@dataclass(frozen=True, slots=True)
class ConflictCandidate:
    session_id: str
    score: float
    start_at: datetime
    end_at: datetime
    factors: dict[str, float]
    source_message_ids: tuple[str, ...]


@dataclass(slots=True)
class ConversationAnalytics:
    conversation_id: str
    message_count: int
    turn_count: int
    session_count: int
    participant_metrics: dict[str, dict[str, Any]] = field(default_factory=dict)
    latency_samples: list[LatencySample] = field(default_factory=list)
    conflicts: list[ConflictCandidate] = field(default_factory=list)
    sessions: list[Session] = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
