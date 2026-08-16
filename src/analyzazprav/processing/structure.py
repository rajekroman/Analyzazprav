from __future__ import annotations

from .models import CanonicalMessage, MessageRelation, SenderRun, Session, Thread
from .text import clean_text

_MAX_ORDER = 2**63 - 1


def canonical_sort_key(message: CanonicalMessage) -> tuple[int, int, int, str, int, int]:
    return (
        1 if message.timestamp_us is None else 0,
        message.timestamp_us if message.timestamp_us is not None else _MAX_ORDER,
        message.source_order if message.source_order is not None else _MAX_ORDER,
        message.source_message_id or "",
        message.id,
        message.membership_id,
    )


def ordered_by_conversation(messages: list[CanonicalMessage]) -> dict[int, list[CanonicalMessage]]:
    grouped: dict[int, list[CanonicalMessage]] = {}
    for message in messages:
        grouped.setdefault(message.conversation_id, []).append(message)
    for values in grouped.values():
        values.sort(key=canonical_sort_key)
    return grouped


def build_sender_runs(
    grouped: dict[int, list[CanonicalMessage]],
    *,
    resolved_sender_map: dict[int, int] | None = None,
) -> tuple[tuple[SenderRun, ...], dict[int, int]]:
    runs: list[SenderRun] = []
    membership_to_run: dict[int, int] = {}
    resolved_sender_map = resolved_sender_map or {}

    def sender_key(message: CanonicalMessage) -> int | None:
        if message.sender_id is None:
            return None
        return resolved_sender_map.get(message.sender_id, message.sender_id)

    next_id = 1
    for conversation_id in sorted(grouped):
        messages = grouped[conversation_id]
        start = 0
        while start < len(messages):
            resolved_sender_id = sender_key(messages[start])
            end = start + 1
            # Unknown sender identity is not evidence that adjacent unknown rows
            # belong to the same person. Keep each unknown row as its own run.
            if resolved_sender_id is not None:
                while end < len(messages) and sender_key(messages[end]) == resolved_sender_id:
                    end += 1
            chunk = messages[start:end]
            raw_sender_ids = {message.sender_id for message in chunk}
            sender_id = next(iter(raw_sender_ids)) if len(raw_sender_ids) == 1 else None
            run = SenderRun(
                id=next_id,
                conversation_id=conversation_id,
                sender_id=sender_id,
                first_membership_id=chunk[0].membership_id,
                last_membership_id=chunk[-1].membership_id,
                first_message_id=chunk[0].id,
                last_message_id=chunk[-1].id,
                start_us=chunk[0].timestamp_us,
                end_us=chunk[-1].timestamp_us,
                message_count=len(chunk),
                char_count=sum(len(clean_text(m.text) or "") for m in chunk),
                resolved_participant_id=resolved_sender_id,
            )
            runs.append(run)
            for message in chunk:
                membership_to_run[message.membership_id] = next_id
            next_id += 1
            start = end
    return tuple(runs), membership_to_run


def build_sessions(
    grouped: dict[int, list[CanonicalMessage]], *, gap_threshold_us: int
) -> tuple[tuple[Session, ...], dict[int, int]]:
    sessions: list[Session] = []
    membership_to_session: dict[int, int] = {}
    next_id = 1
    for conversation_id in sorted(grouped):
        messages = grouped[conversation_id]
        start = 0
        for idx in range(1, len(messages) + 1):
            boundary = idx == len(messages)
            if not boundary:
                previous = messages[idx - 1]
                current = messages[idx]
                boundary = (
                    previous.timestamp_us is None
                    or current.timestamp_us is None
                    or current.timestamp_us - previous.timestamp_us > gap_threshold_us
                )
            if not boundary:
                continue
            chunk = messages[start:idx]
            if not chunk:
                continue
            session = Session(
                id=next_id,
                conversation_id=conversation_id,
                first_membership_id=chunk[0].membership_id,
                last_membership_id=chunk[-1].membership_id,
                first_message_id=chunk[0].id,
                last_message_id=chunk[-1].id,
                start_us=chunk[0].timestamp_us,
                end_us=chunk[-1].timestamp_us,
                message_count=len(chunk),
                gap_threshold_us=gap_threshold_us,
            )
            sessions.append(session)
            for message in chunk:
                membership_to_session[message.membership_id] = next_id
            next_id += 1
            start = idx
    return tuple(sessions), membership_to_session


def build_explicit_threads(
    messages: list[CanonicalMessage],
    relations: list[MessageRelation],
    *,
    session_map: dict[int, int],
    reply_relation_types: frozenset[str],
) -> tuple[tuple[Thread, ...], dict[int, int]]:
    by_membership = {message.membership_id: message for message in messages}
    memberships_by_message: dict[int, list[CanonicalMessage]] = {}
    for message in messages:
        memberships_by_message.setdefault(message.id, []).append(message)

    adjacency: dict[int, set[int]] = {}
    for relation in relations:
        if relation.relation_type not in reply_relation_types:
            continue
        sources = memberships_by_message.get(relation.source_message_id, ())
        targets = memberships_by_message.get(relation.target_message_id, ())
        for source in sources:
            for target in targets:
                if source.conversation_id != target.conversation_id:
                    continue
                if source.membership_id == target.membership_id:
                    continue
                adjacency.setdefault(source.membership_id, set()).add(target.membership_id)
                adjacency.setdefault(target.membership_id, set()).add(source.membership_id)

    threads: list[Thread] = []
    membership_to_thread: dict[int, int] = {}
    visited: set[int] = set()
    next_id = 1
    for start in sorted(adjacency):
        if start in visited:
            continue
        stack = [start]
        component: set[int] = set()
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            component.add(current)
            stack.extend(sorted(adjacency.get(current, ()), reverse=True))
        if len(component) < 2:
            continue
        ordered_memberships = tuple(
            sorted(component, key=lambda membership_id: canonical_sort_key(by_membership[membership_id]))
        )
        members = [by_membership[membership_id] for membership_id in ordered_memberships]
        first = members[0]
        if any(member.conversation_id != first.conversation_id for member in members):
            raise RuntimeError("A3 thread component crossed conversation memberships")
        component_sessions = {session_map[membership_id] for membership_id in ordered_memberships}
        thread = Thread(
            id=next_id,
            conversation_id=first.conversation_id,
            session_id=next(iter(component_sessions)) if len(component_sessions) == 1 else None,
            membership_ids=ordered_memberships,
            message_ids=tuple(member.id for member in members),
            method="explicit_reply_membership_component_v2",
            confidence=1.0,
        )
        threads.append(thread)
        for membership_id in ordered_memberships:
            membership_to_thread[membership_id] = next_id
        next_id += 1
    return tuple(threads), membership_to_thread
