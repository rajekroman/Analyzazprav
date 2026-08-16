from __future__ import annotations

import unicodedata

from .models import (
    CanonicalParticipant,
    ParticipantAlias,
    ParticipantResolutionCandidate,
    ResolvedParticipant,
)


def _normalized_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", value)
    normalized = " ".join(normalized.split()).casefold()
    return normalized or None


def resolve_participants(
    participants: list[CanonicalParticipant] | tuple[CanonicalParticipant, ...],
    *,
    sender_ids: set[int] | frozenset[int] | None = None,
) -> tuple[
    tuple[ResolvedParticipant, ...],
    tuple[ParticipantAlias, ...],
    tuple[ParticipantResolutionCandidate, ...],
    dict[int, int],
]:
    """Build conservative A3 person groups without modifying A2 participants.

    Cross-participant automatic union is intentionally limited to participants
    explicitly marked ``is_self`` by A2. Equal display names are only candidates.
    """
    by_id: dict[int, CanonicalParticipant] = {}
    for participant in participants:
        if participant.id in by_id:
            raise ValueError(f"duplicate participant id in A2 projection: {participant.id}")
        by_id[participant.id] = participant

    for sender_id in sorted(sender_ids or ()):
        if sender_id not in by_id:
            by_id[sender_id] = CanonicalParticipant(sender_id, None, False, ())

    self_ids = tuple(sorted(pid for pid, participant in by_id.items() if participant.is_self))
    clusters: list[tuple[int, ...]] = []
    if self_ids:
        clusters.append(self_ids)
    clusters.extend((pid,) for pid in sorted(by_id) if pid not in self_ids)
    clusters.sort(key=lambda members: members[0])

    resolved: list[ResolvedParticipant] = []
    aliases: list[ParticipantAlias] = []
    participant_to_resolved: dict[int, int] = {}

    for members in clusters:
        resolved_id = members[0]
        is_self = any(by_id[participant_id].is_self for participant_id in members)
        names = [
            by_id[participant_id].canonical_name
            for participant_id in members
            if by_id[participant_id].canonical_name
            and by_id[participant_id].canonical_name.strip()
        ]
        method = (
            "explicit_is_self_union_v1"
            if is_self and len(members) > 1
            else "a2_participant_membership_v1"
        )
        resolved.append(
            ResolvedParticipant(
                id=resolved_id,
                canonical_name=names[0] if names else None,
                is_self=is_self,
                member_participant_ids=members,
                method=method,
                confidence=1.0,
            )
        )
        for participant_id in members:
            participant_to_resolved[participant_id] = resolved_id
            participant = by_id[participant_id]
            for identity in sorted(participant.identities, key=lambda item: item.id):
                aliases.append(
                    ParticipantAlias(
                        resolved_participant_id=resolved_id,
                        participant_id=participant_id,
                        participant_identity_id=identity.id,
                        identity_type=identity.identity_type,
                        normalized_value=identity.normalized_value,
                        original_value=identity.original_value,
                        method=(
                            "explicit_is_self_alias_v1"
                            if method == "explicit_is_self_union_v1"
                            else "a2_identity_membership_v1"
                        ),
                        confidence=1.0,
                    )
                )

    candidates: list[ParticipantResolutionCandidate] = []
    name_groups: dict[str, list[int]] = {}
    for participant_id in sorted(by_id):
        name = _normalized_name(by_id[participant_id].canonical_name)
        if name is not None:
            name_groups.setdefault(name, []).append(participant_id)

    for name in sorted(name_groups):
        ids = name_groups[name]
        for left, right in zip(ids, ids[1:]):
            if participant_to_resolved[left] == participant_to_resolved[right]:
                continue
            a, b = sorted((left, right))
            candidates.append(
                ParticipantResolutionCandidate(
                    left_participant_id=a,
                    right_participant_id=b,
                    reason="same_normalized_canonical_name",
                    confidence=0.35,
                    method="normalized_canonical_name_candidate_v1",
                )
            )

    return tuple(resolved), tuple(aliases), tuple(candidates), participant_to_resolved
