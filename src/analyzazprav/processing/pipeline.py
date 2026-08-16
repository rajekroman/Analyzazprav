from __future__ import annotations

from dataclasses import dataclass

from .dedup import audit_duplicate_candidates
from .features import build_features
from .models import CanonicalMessage, CanonicalParticipant, MessageRelation, ProcessedMessage, ProcessingResult
from .participants import resolve_participants
from .structure import (
    build_explicit_threads,
    build_sender_runs,
    build_sessions,
    ordered_by_conversation,
)
from .text import clean_text

PROCESSING_VERSION = "6"


@dataclass(frozen=True, slots=True)
class ProcessingConfig:
    session_gap_seconds: int = 6 * 60 * 60
    duplicate_tolerance_seconds: int = 2
    reply_relation_types: frozenset[str] = frozenset({"reply", "reply_to"})

    def __post_init__(self) -> None:
        if self.session_gap_seconds <= 0:
            raise ValueError("session_gap_seconds must be positive")
        if self.duplicate_tolerance_seconds < 0:
            raise ValueError("duplicate_tolerance_seconds cannot be negative")


def process_messages(
    messages: list[CanonicalMessage],
    relations: list[MessageRelation] | None = None,
    config: ProcessingConfig | None = None,
    *,
    participants: list[CanonicalParticipant] | tuple[CanonicalParticipant, ...] | None = None,
) -> ProcessingResult:
    cfg = config or ProcessingConfig()
    membership_ids = [message.membership_id for message in messages]
    if len(set(membership_ids)) != len(membership_ids):
        raise ValueError("A3 input contains duplicate membership_id values")

    (
        resolved_participants,
        participant_aliases,
        participant_resolution_candidates,
        resolved_sender_map,
    ) = resolve_participants(
        participants or (),
        sender_ids={message.sender_id for message in messages if message.sender_id is not None},
    )

    grouped = ordered_by_conversation(messages)
    sender_runs, run_map = build_sender_runs(grouped, resolved_sender_map=resolved_sender_map)
    sessions, session_map = build_sessions(
        grouped, gap_threshold_us=cfg.session_gap_seconds * 1_000_000
    )
    threads, thread_map = build_explicit_threads(
        messages,
        relations or [],
        session_map=session_map,
        reply_relation_types=cfg.reply_relation_types,
    )
    feature_map = build_features(grouped, resolved_sender_map=resolved_sender_map)

    processed: list[ProcessedMessage] = []
    for conversation_id in sorted(grouped):
        for sequence_number, message in enumerate(grouped[conversation_id], start=1):
            resolved_sender_id = (
                None if message.sender_id is None else resolved_sender_map.get(message.sender_id)
            )
            processed.append(
                ProcessedMessage(
                    membership_id=message.membership_id,
                    message_id=message.id,
                    conversation_id=conversation_id,
                    sequence_number=sequence_number,
                    text_clean=clean_text(message.text),
                    sender_run_id=run_map[message.membership_id],
                    session_id=session_map[message.membership_id],
                    thread_id=thread_map.get(message.membership_id),
                    features=feature_map[message.membership_id],
                    resolved_sender_id=resolved_sender_id,
                )
            )

    duplicates = audit_duplicate_candidates(
        messages,
        tolerance_us=cfg.duplicate_tolerance_seconds * 1_000_000,
    )
    return ProcessingResult(
        messages=tuple(processed),
        sender_runs=sender_runs,
        sessions=sessions,
        threads=threads,
        duplicate_candidates=duplicates,
        resolved_participants=resolved_participants,
        participant_aliases=participant_aliases,
        participant_resolution_candidates=participant_resolution_candidates,
    )
