from __future__ import annotations

from itertools import combinations

from .models import CanonicalMessage, DuplicateCandidate
from .text import clean_text


def audit_duplicate_candidates(
    messages: list[CanonicalMessage], *, tolerance_us: int
) -> tuple[DuplicateCandidate, ...]:
    """Flag suspicious A2 canonical pairs without merging or deleting anything."""
    groups: dict[tuple[int, int | None, str | None, tuple[str, ...]], list[CanonicalMessage]] = {}
    for message in messages:
        key = (
            message.conversation_id,
            message.sender_id,
            clean_text(message.text),
            tuple(sorted(message.attachment_keys)),
        )
        groups.setdefault(key, []).append(message)

    candidates: list[DuplicateCandidate] = []
    for group_key in sorted(groups, key=repr):
        group = sorted(groups[group_key], key=lambda m: m.id)
        for left, right in combinations(group, 2):
            if left.source_message_id and left.source_message_id == right.source_message_id:
                candidates.append(
                    DuplicateCandidate(left.id, right.id, "exact_source_identity", 1.0, "source_message_id_v1")
                )
                continue
            if left.timestamp_us is None or right.timestamp_us is None:
                continue
            if abs(right.timestamp_us - left.timestamp_us) <= tolerance_us:
                candidates.append(
                    DuplicateCandidate(left.id, right.id, "probable_cross_export", 0.80, "temporal_content_v1")
                )
    return tuple(candidates)
