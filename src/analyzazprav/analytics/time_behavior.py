from __future__ import annotations

from collections import defaultdict
from typing import Sequence

from .config import AnalyticsConfig
from .models import AnalyticMessage, SilenceEvent, TimeBucketMetric, Turn


def _is_night(hour: int, start_hour: int, end_hour: int) -> bool:
    """Return whether hour belongs to the configured half-open night interval."""

    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def build_time_buckets(
    messages: Sequence[AnalyticMessage], config: AnalyticsConfig
) -> list[TimeBucketMetric]:
    """Build deterministic clock/weekday distributions from A3 calendar fields.

    Local time is preferred message-by-message. UTC is used only when A3 could
    not derive the corresponding local calendar field. Local and UTC fallback
    evidence is never silently merged into the same bucket.
    """

    grouped: dict[tuple[int, str, str, str], list[int]] = defaultdict(list)
    if not messages:
        return []

    for message in messages:
        if message.participant_id is None:
            continue
        participant_id = int(message.participant_id)

        if message.local_hour is not None:
            hour = int(message.local_hour)
            hour_basis = "local"
        elif message.utc_hour is not None:
            hour = int(message.utc_hour)
            hour_basis = "utc"
        else:
            hour = None
            hour_basis = None

        if hour is not None and hour_basis is not None:
            grouped[(participant_id, hour_basis, "hour", f"{hour:02d}")].append(
                message.message_id
            )
            daypart = (
                "night"
                if _is_night(hour, config.night_start_hour, config.night_end_hour)
                else "non_night"
            )
            grouped[(participant_id, hour_basis, "night", daypart)].append(
                message.message_id
            )

        if message.local_weekday is not None:
            weekday = int(message.local_weekday)
            weekday_basis = "local"
        elif message.utc_weekday is not None:
            weekday = int(message.utc_weekday)
            weekday_basis = "utc"
        else:
            weekday = None
            weekday_basis = None

        if weekday is not None and weekday_basis is not None:
            grouped[(participant_id, weekday_basis, "weekday", str(weekday))].append(
                message.message_id
            )
            weekpart = "weekend" if weekday >= 5 else "weekday"
            grouped[(participant_id, weekday_basis, "weekend", weekpart)].append(
                message.message_id
            )

    rows: list[TimeBucketMetric] = []
    for (participant_id, basis, kind, value), message_ids in sorted(grouped.items()):
        rows.append(
            TimeBucketMetric(
                conversation_id=messages[0].conversation_id,
                participant_id=participant_id,
                time_basis=basis,
                bucket_kind=kind,
                bucket_value=value,
                message_count=len(message_ids),
                source_message_ids=tuple(message_ids),
            )
        )
    return rows


def build_silence_events(
    turns: Sequence[Turn], config: AnalyticsConfig
) -> list[SilenceEvent]:
    """Detect long inter-session gaps and identify who resumes contact.

    A3 owns session boundaries. A4 only evaluates gaps between consecutive A3
    sessions. If either boundary timestamp is unknown, no duration is invented.
    """

    grouped: dict[int, list[Turn]] = defaultdict(list)
    for turn in turns:
        grouped[turn.session_id].append(turn)
    session_ids = sorted(grouped, key=lambda sid: grouped[sid][0].turn_id)
    events: list[SilenceEvent] = []

    for previous_session_id, next_session_id in zip(session_ids, session_ids[1:]):
        previous_turns = grouped[previous_session_id]
        next_turns = grouped[next_session_id]
        boundary_before = previous_turns[-1]
        boundary_after = next_turns[0]
        if boundary_before.end_us is None or boundary_after.start_us is None:
            continue
        gap_us = boundary_after.start_us - boundary_before.end_us
        if gap_us < 0:
            continue
        gap_seconds = gap_us / 1_000_000
        if gap_seconds < config.long_silence_seconds:
            continue

        before_known = next(
            (turn for turn in reversed(previous_turns) if turn.participant_id is not None),
            None,
        )
        return_known = next(
            (turn for turn in next_turns if turn.participant_id is not None),
            None,
        )
        evidence_ids = tuple(
            dict.fromkeys(boundary_before.message_ids + boundary_after.message_ids)
        )
        events.append(
            SilenceEvent(
                conversation_id=boundary_before.conversation_id,
                previous_session_id=previous_session_id,
                next_session_id=next_session_id,
                gap_seconds=gap_seconds,
                previous_turn_id=boundary_before.turn_id,
                return_turn_id=boundary_after.turn_id,
                before_participant_id=(
                    None if before_known is None else before_known.participant_id
                ),
                return_participant_id=(
                    None if return_known is None else return_known.participant_id
                ),
                source_message_ids=evidence_ids,
            )
        )
    return events
