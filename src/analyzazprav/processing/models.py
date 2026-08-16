from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ParticipantIdentity:
    id: int
    participant_id: int
    identity_type: str
    normalized_value: str
    original_value: str | None = None


@dataclass(frozen=True, slots=True)
class CanonicalParticipant:
    id: int
    canonical_name: str | None
    is_self: bool
    identities: tuple[ParticipantIdentity, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolvedParticipant:
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

    @property
    def dedup_key(self) -> str:
        return self.sha256 or f"attachment:{self.id}:{self.availability}"


@dataclass(frozen=True, slots=True)
class CanonicalMessage:
    """Read-only A2 projection of one message-conversation membership."""

    membership_id: int
    id: int
    conversation_id: int
    sender_id: int | None
    timestamp_us: int | None
    text: str | None = None
    source_message_id: str | None = None
    source_record_keys: tuple[str, ...] = ()
    source_order: int | None = None
    timezone_offset_min: int | None = None
    message_type: str = "text"
    attachments: tuple[AttachmentRef, ...] = ()


@dataclass(frozen=True, slots=True)
class MessageRelation:
    """Canonical A2 message-level relation; A3 resolves it to shared memberships."""

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
    first_membership_id: int
    last_membership_id: int
    first_message_id: int
    last_message_id: int
    start_us: int | None
    end_us: int | None
    message_count: int
    char_count: int
    resolved_participant_id: int | None = None
    method: str = "deterministic_resolved_sender_run_v4"


@dataclass(frozen=True, slots=True)
class Session:
    id: int
    conversation_id: int
    first_membership_id: int
    last_membership_id: int
    first_message_id: int
    last_message_id: int
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
    membership_ids: tuple[int, ...]
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
    membership_id: int
    message_id: int
    conversation_id: int
    sequence_number: int
    text_clean: str | None
    sender_run_id: int
    session_id: int
    thread_id: int | None
    features: MessageFeatures
    resolved_sender_id: int | None = None


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
