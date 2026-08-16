from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


MessageOccurrenceKey = tuple[int, int]


@dataclass(frozen=True, slots=True)
class ParticipantIdentity:
    id: int
    participant_id: int
    identity_type: str
    normalized_value: str
    original_value: str | None = None


@dataclass(frozen=True, slots=True)
class CanonicalParticipant:
    """Read-only projection of one A2 canonical participant."""

    id: int
    canonical_name: str | None
    is_self: bool
    identities: tuple[ParticipantIdentity, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolvedParticipant:
    """A3 derived person grouping over one or more A2 participants."""

    id: int
    canonical_name: str | None
    is_self: bool
    member_participant_ids: tuple[int, ...]
    method: str
    confidence: float


@dataclass(frozen=True, slots=True)
class ParticipantAlias:
    resolved_participant_id: int
    participant_id: int
    participant_identity_id: int
    identity_type: str
    normalized_value: str
    original_value: str | None
    method: str
    confidence: float


@dataclass(frozen=True, slots=True)
class ParticipantResolutionCandidate:
    left_participant_id: int
    right_participant_id: int
    reason: str
    confidence: float
    method: str


@dataclass(frozen=True, slots=True)
class AttachmentRef:
    id: int
    sha256: str | None
    mime_type: str | None
    size_bytes: int | None
    filename: str | None
    availability: str
    position: int | None
    media_type: str
    occurrence_id: int | None = None

    @property
    def dedup_key(self) -> str:
        return self.sha256 or f"attachment:{self.id}:{self.availability}"


@dataclass(frozen=True, slots=True)
class CanonicalMessage:
    """One A2 message-conversation membership projected for A3 processing."""

    id: int
    conversation_id: int
    sender_id: int | None
    timestamp_us: int | None
    text: str | None = None
    source_message_id: str | None = None
    source_order: int | None = None
    timezone_offset_min: int | None = None
    message_type: str = "text"
    attachments: tuple[AttachmentRef, ...] = ()
    membership_id: int | None = None

    @property
    def occurrence_key(self) -> MessageOccurrenceKey:
        return (self.conversation_id, self.id)


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
    participants: tuple[CanonicalParticipant, ...] = field(default_factory=tuple)


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
    resolved_participant_id: int | None
    first_message_id: int
    last_message_id: int
    first_membership_id: int | None
    last_membership_id: int | None
    start_us: int | None
    end_us: int | None
    message_count: int
    char_count: int
    method: str = "deterministic_resolved_sender_run_v2"


@dataclass(frozen=True, slots=True)
class Session:
    id: int
    conversation_id: int
    first_message_id: int
    last_message_id: int
    first_membership_id: int | None
    last_membership_id: int | None
    start_us: int | None
    end_us: int | None
    message_count: int
    gap_threshold_us: int
    method: str = "temporal_gap_v2"


@dataclass(frozen=True, slots=True)
class Thread:
    id: int
    conversation_id: int
    session_id: int | None
    message_ids: tuple[int, ...]
    method: str
    confidence: float
    membership_ids: tuple[int | None, ...] = ()


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
    attachment_count: int
    image_count: int
    gif_count: int
    video_count: int
    audio_count: int
    document_count: int
    other_media_count: int
    missing_attachment_count: int
    seconds_since_previous_message: float | None
    seconds_since_previous_other_sender: float | None
    utc_year: int | None
    utc_month: int | None
    utc_day: int | None
    utc_weekday: int | None
    utc_hour: int | None
    local_year: int | None
    local_month: int | None
    local_day: int | None
    local_weekday: int | None
    local_hour: int | None


@dataclass(frozen=True, slots=True)
class ProcessedMessage:
    message_id: int
    conversation_id: int
    membership_id: int | None
    sequence_number: int
    text_clean: str | None
    sender_run_id: int
    session_id: int
    thread_id: int | None
    resolved_sender_id: int | None
    features: MessageFeatures

    @property
    def occurrence_key(self) -> MessageOccurrenceKey:
        return (self.conversation_id, self.message_id)


@dataclass(frozen=True, slots=True)
class ProcessingResult:
    messages: tuple[ProcessedMessage, ...]
    sender_runs: tuple[SenderRun, ...]
    sessions: tuple[Session, ...]
    threads: tuple[Thread, ...] = field(default_factory=tuple)
    duplicate_candidates: tuple[DuplicateCandidate, ...] = field(default_factory=tuple)
    resolved_participants: tuple[ResolvedParticipant, ...] = field(default_factory=tuple)
    participant_aliases: tuple[ParticipantAlias, ...] = field(default_factory=tuple)
    participant_resolution_candidates: tuple[ParticipantResolutionCandidate, ...] = field(default_factory=tuple)
