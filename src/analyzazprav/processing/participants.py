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
    """Build conservative A3 person groups without modifying A2 canonical participants.

    The only cross-participant merge performed by default is between A2 participants
    explicitly marked ``is_self``. Canonical-name equality is retained only as an
    audit candidate and never causes a merge.
    """
    by_id: dict[int, CanonicalParticipant] = {}
    for participant in participants:
        if participant.id in by_id:
            raise ValueError(f"duplicate participant id in A2 projection: {participant.id}")
        for identity in participant.identities:
            if identity.participant_id != participant.id:
                raise ValueError(
                    f"participant identity {identity.id} belongs to {identity.participant_id}, "
                    f"not {participant.id}"
                )
        by_id[participant.id] = participant

    for sender_id in sorted(sender_ids or ()):
        if sender_id not in by_id:
            by_id[sender_id] = CanonicalParticipant(
                id=sender_id,
                canonical_name=None,
                is_self=False,
                identities=(),
            )

    self_ids = tuple(sorted(pid for pid, p in by_id.items() if p.is_self))
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
        is_self = any(by_id[pid].is_self for pid in members)
        names = [
            by_id[pid].canonical_name
            for pid in members
            if by_id[pid].canonical_name and by_id[pid].canonical_name.strip()
        ]
        canonical_name = names[0] if names else None
        method = (
            "explicit_is_self_union_v1"
            if is_self and len(members) > 1
            else "a2_participant_membership_v1"
        )
        resolved.append(
            ResolvedParticipant(
                id=resolved_id,
                canonical_name=canonical_name,
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
        key = _normalized_name(by_id[participant_id].canonical_name)
        if key is not None:
            name_groups.setdefault(key, []).append(participant_id)

    for key in sorted(name_groups):
        participant_ids = name_groups[key]
        for left, right in zip(participant_ids, participant_ids[1:]):
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

    return (
        tuple(resolved),
        tuple(aliases),
        tuple(candidates),
        participant_to_resolved,
    )
