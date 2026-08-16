from __future__ import annotations

from .models import CanonicalMessage, DuplicateCandidate
from .text import clean_text


def _candidate(
    left: CanonicalMessage,
    right: CanonicalMessage,
    classification: str,
    confidence: float,
    method: str,
) -> DuplicateCandidate:
    message_id_a, message_id_b = sorted((left.id, right.id))
    return DuplicateCandidate(message_id_a, message_id_b, classification, confidence, method)


def audit_duplicate_candidates(
    messages: list[CanonicalMessage], *, tolerance_us: int
) -> tuple[DuplicateCandidate, ...]:
    """Flag suspicious A2 canonical pairs without quadratic all-pairs comparison."""
    groups: dict[tuple[int, int | None, str | None, tuple[str, ...]], list[CanonicalMessage]] = {}
    for message in messages:
        key = (
            message.conversation_id,
            message.sender_id,
            clean_text(message.text),
            tuple(sorted(attachment.dedup_key for attachment in message.attachments)),
        )
        groups.setdefault(key, []).append(message)

    candidates: list[DuplicateCandidate] = []
    seen: set[tuple[int, int, str]] = set()

    def append(candidate: DuplicateCandidate) -> None:
        key = (candidate.left_message_id, candidate.right_message_id, candidate.classification)
        if key not in seen:
            seen.add(key)
            candidates.append(candidate)

    for group_key in sorted(groups, key=repr):
        group = groups[group_key]

        # Same stable source ID is strong evidence even when export timestamps differ.
        first_by_source_id: dict[str, CanonicalMessage] = {}
        for message in sorted(group, key=lambda item: item.id):
            if not message.source_message_id:
                continue
            first = first_by_source_id.setdefault(message.source_message_id, message)
            if first.id != message.id:
                append(_candidate(first, message, "exact_source_identity", 1.0, "source_message_id_v1"))

        # For weaker cross-export evidence, adjacent equal-content records in time order
        # are sufficient to connect a dense duplicate cluster without O(n²) pairs.
        timestamped = sorted(
            (message for message in group if message.timestamp_us is not None),
            key=lambda item: (item.timestamp_us, item.id),
        )
        for previous, current in zip(timestamped, timestamped[1:]):
            if current.timestamp_us - previous.timestamp_us > tolerance_us:
                continue
            if previous.source_message_id and previous.source_message_id == current.source_message_id:
                continue
            append(_candidate(previous, current, "probable_cross_export", 0.80, "temporal_content_adjacent_v2"))

    return tuple(candidates)
