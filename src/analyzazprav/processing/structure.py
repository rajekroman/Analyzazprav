from __future__ import annotations

from .models import CanonicalMessage, MessageOccurrenceKey, MessageRelation, SenderRun, Session, Thread
from .text import clean_text

_MAX_ORDER = 2**63 - 1


def canonical_sort_key(message: CanonicalMessage) -> tuple[int, int, int, str, int, int]:
    return (
        1 if message.timestamp_us is None else 0,
        message.timestamp_us if message.timestamp_us is not None else _MAX_ORDER,
        message.source_order if message.source_order is not None else _MAX_ORDER,
        message.source_message_id or "",
        message.membership_id if message.membership_id is not None else _MAX_ORDER,
        message.id,
    )


def ordered_by_conversation(messages: list[CanonicalMessage]) -> dict[int, list[CanonicalMessage]]:
    grouped: dict[int, list[CanonicalMessage]] = {}
    seen: set[MessageOccurrenceKey] = set()
    for message in messages:
        if message.occurrence_key in seen:
            raise ValueError(
                f"duplicate message-conversation membership in A3 projection: "
                f"conversation={message.conversation_id} message={message.id}"
            )
        seen.add(message.occurrence_key)
        grouped.setdefault(message.conversation_id, []).append(message)
    for values in grouped.values():
        values.sort(key=canonical_sort_key)
    return grouped


def build_sender_runs(
    grouped: dict[int, list[CanonicalMessage]],
    *,
    resolved_sender_map: dict[int, int] | None = None,
) -> tuple[tuple[SenderRun, ...], dict[MessageOccurrenceKey, int]]:
    runs: list[SenderRun] = []
    message_to_run: dict[MessageOccurrenceKey, int] = {}
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
            while end < len(messages) and sender_key(messages[end]) == resolved_sender_id:
                end += 1
            chunk = messages[start:end]
            raw_sender_ids = {message.sender_id for message in chunk}
            raw_sender_id = next(iter(raw_sender_ids)) if len(raw_sender_ids) == 1 else None
            run = SenderRun(
                id=next_id,
                conversation_id=conversation_id,
                sender_id=raw_sender_id,
                resolved_participant_id=resolved_sender_id,
                first_message_id=chunk[0].id,
                last_message_id=chunk[-1].id,
                first_membership_id=chunk[0].membership_id,
                last_membership_id=chunk[-1].membership_id,
                start_us=chunk[0].timestamp_us,
                end_us=chunk[-1].timestamp_us,
                message_count=len(chunk),
                char_count=sum(len(clean_text(m.text) or "") for m in chunk),
            )
            runs.append(run)
            for message in chunk:
                message_to_run[message.occurrence_key] = next_id
            next_id += 1
            start = end
    return tuple(runs), message_to_run


def build_sessions(
    grouped: dict[int, list[CanonicalMessage]], *, gap_threshold_us: int
) -> tuple[tuple[Session, ...], dict[MessageOccurrenceKey, int]]:
    sessions: list[Session] = []
    message_to_session: dict[MessageOccurrenceKey, int] = {}
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
                first_message_id=chunk[0].id,
                last_message_id=chunk[-1].id,
                first_membership_id=chunk[0].membership_id,
                last_membership_id=chunk[-1].membership_id,
                start_us=chunk[0].timestamp_us,
                end_us=chunk[-1].timestamp_us,
                message_count=len(chunk),
                gap_threshold_us=gap_threshold_us,
            )
            sessions.append(session)
            for message in chunk:
                message_to_session[message.occurrence_key] = next_id
            next_id += 1
            start = idx
    return tuple(sessions), message_to_session


def _relation_conversation_hint(relation: MessageRelation) -> int | None:
    value = relation.metadata.get("conversation_id")
    if isinstance(value, bool):
        return None
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def build_explicit_threads(
    messages: list[CanonicalMessage],
    relations: list[MessageRelation],
    *,
    session_map: dict[MessageOccurrenceKey, int],
    reply_relation_types: frozenset[str],
) -> tuple[tuple[Thread, ...], dict[MessageOccurrenceKey, int]]:
    """Build source-evidenced reply components without guessing ambiguous memberships."""
    by_key = {message.occurrence_key: message for message in messages}
    by_message_id: dict[int, dict[int, CanonicalMessage]] = {}
    for message in messages:
        by_message_id.setdefault(message.id, {})[message.conversation_id] = message

    adjacency: dict[MessageOccurrenceKey, set[MessageOccurrenceKey]] = {}
    for relation in relations:
        if relation.relation_type not in reply_relation_types:
            continue
        source_by_conversation = by_message_id.get(relation.source_message_id, {})
        target_by_conversation = by_message_id.get(relation.target_message_id, {})
        common = sorted(set(source_by_conversation) & set(target_by_conversation))
        if not common:
            continue

        if len(common) == 1:
            selected = common
        else:
            hint = _relation_conversation_hint(relation)
            selected = [hint] if hint in common else []

        for conversation_id in selected:
            source_key = source_by_conversation[conversation_id].occurrence_key
            target_key = target_by_conversation[conversation_id].occurrence_key
            if source_key == target_key:
                continue
            adjacency.setdefault(source_key, set()).add(target_key)
            adjacency.setdefault(target_key, set()).add(source_key)

    threads: list[Thread] = []
    message_to_thread: dict[MessageOccurrenceKey, int] = {}
    visited: set[MessageOccurrenceKey] = set()
    next_id = 1
    for start in sorted(adjacency):
        if start in visited:
            continue
        stack = [start]
        component: set[MessageOccurrenceKey] = set()
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            component.add(current)
            stack.extend(sorted(adjacency.get(current, ()), reverse=True))
        if len(component) < 2:
            continue

        ordered_keys = tuple(sorted(component, key=lambda key: canonical_sort_key(by_key[key])))
        ordered_messages = tuple(by_key[key] for key in ordered_keys)
        conversation_ids = {message.conversation_id for message in ordered_messages}
        if len(conversation_ids) != 1:
            continue
        component_sessions = {session_map[key] for key in ordered_keys}
        thread = Thread(
            id=next_id,
            conversation_id=ordered_messages[0].conversation_id,
            session_id=next(iter(component_sessions)) if len(component_sessions) == 1 else None,
            message_ids=tuple(message.id for message in ordered_messages),
            membership_ids=tuple(message.membership_id for message in ordered_messages),
            method="explicit_reply_component_v2",
            confidence=1.0,
        )
        threads.append(thread)
        for key in ordered_keys:
            message_to_thread[key] = next_id
        next_id += 1
    return tuple(threads), message_to_thread
