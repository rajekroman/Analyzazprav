from __future__ import annotations

from dataclasses import dataclass

from .dedup import audit_duplicate_candidates
from .features import build_features
from .models import CanonicalMessage, MessageRelation, ProcessedMessage, ProcessingResult
from .structure import (
    build_explicit_threads,
    build_sender_runs,
    build_sessions,
    ordered_by_conversation,
)
from .text import clean_text

PROCESSING_VERSION = "4"


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
) -> ProcessingResult:
    cfg = config or ProcessingConfig()
    membership_ids = [message.membership_id for message in messages]
    if len(set(membership_ids)) != len(membership_ids):
        raise ValueError("A3 input contains duplicate membership_id values")

    grouped = ordered_by_conversation(messages)
    sender_runs, run_map = build_sender_runs(grouped)
    sessions, session_map = build_sessions(
        grouped, gap_threshold_us=cfg.session_gap_seconds * 1_000_000
    )
    threads, thread_map = build_explicit_threads(
        messages,
        relations or [],
        session_map=session_map,
        reply_relation_types=cfg.reply_relation_types,
    )
    feature_map = build_features(grouped)

    processed: list[ProcessedMessage] = []
    for conversation_id in sorted(grouped):
        for sequence_number, message in enumerate(grouped[conversation_id], start=1):
            membership_id = message.membership_id
            processed.append(
                ProcessedMessage(
                    membership_id=membership_id,
                    message_id=message.id,
                    conversation_id=conversation_id,
                    sequence_number=sequence_number,
                    text_clean=clean_text(message.text),
                    sender_run_id=run_map[membership_id],
                    session_id=session_map[membership_id],
                    thread_id=thread_map.get(membership_id),
                    features=feature_map[membership_id],
                )
            )

    duplicates = audit_duplicate_candidates(
        messages,
        tolerance_us=cfg.duplicate_tolerance_seconds * 1_000_000,
    )
    return ProcessingResult(tuple(processed), sender_runs, sessions, threads, duplicates)
