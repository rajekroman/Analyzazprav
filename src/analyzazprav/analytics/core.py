from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median
from typing import Iterable, Sequence

from .config import AnalyticsConfig
from .models import AnalyticMessage, ConflictCandidate, ConversationAnalytics, ResponseSample, Turn
from .trends import build_daily_metrics, detect_change_points


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _marker_hits(text: str, markers: Sequence[str]) -> int:
    lowered = text.casefold()
    return sum(lowered.count(marker.casefold()) for marker in markers if marker)


def build_turns(messages: Iterable[AnalyticMessage]) -> list[Turn]:
    """Group consecutive A3-processed messages by session and sender.

    A3 owns session boundaries. A4 therefore never joins messages across two
    A3 sessions, even when the sender is the same on both sides of the gap.
    """

    ordered = sorted(messages, key=lambda m: (m.sequence_number, m.message_id))
    if not ordered:
        return []
    conversation_ids = {m.conversation_id for m in ordered}
    if len(conversation_ids) != 1:
        raise ValueError("build_turns expects exactly one conversation")

    turns: list[Turn] = []
    batch: list[AnalyticMessage] = []

    def flush() -> None:
        if not batch:
            return
        turns.append(
            Turn(
                turn_id=len(turns) + 1,
                conversation_id=batch[0].conversation_id,
                session_id=batch[0].session_id,
                participant_id=batch[0].participant_id,
                start_us=batch[0].timestamp_us,
                end_us=batch[-1].timestamp_us,
                message_ids=tuple(message.message_id for message in batch),
                message_count=len(batch),
                word_count=sum(message.word_count for message in batch),
                character_count=sum(message.character_count for message in batch),
                question_mark_count=sum(message.question_mark_count for message in batch),
                exclamation_mark_count=sum(message.exclamation_mark_count for message in batch),
                text="\n".join(message.text_clean for message in batch if message.text_clean),
            )
        )

    for message in ordered:
        if not batch:
            batch.append(message)
            continue
        same_session = message.session_id == batch[-1].session_id
        same_sender = message.participant_id == batch[-1].participant_id
        if same_session and same_sender:
            batch.append(message)
        else:
            flush()
            batch = [message]
    flush()
    return turns


def response_samples(turns: Sequence[Turn]) -> list[ResponseSample]:
    samples: list[ResponseSample] = []
    for previous, current in zip(turns, turns[1:]):
        if previous.session_id != current.session_id:
            continue
        if previous.participant_id is None or current.participant_id is None:
            continue
        if previous.participant_id == current.participant_id:
            continue
        latency_seconds = None
        if previous.end_us is not None and current.start_us is not None:
            latency_us = current.start_us - previous.end_us
            if latency_us >= 0:
                latency_seconds = latency_us / 1_000_000
        effort_ratio = current.word_count / max(1, previous.word_count)
        samples.append(
            ResponseSample(
                conversation_id=current.conversation_id,
                session_id=current.session_id,
                from_participant_id=previous.participant_id,
                responder_id=current.participant_id,
                previous_turn_id=previous.turn_id,
                response_turn_id=current.turn_id,
                latency_seconds=latency_seconds,
                response_effort_ratio=effort_ratio,
            )
        )
    return samples


def _reciprocity(participants: dict[int, dict[str, object]]) -> dict[str, float | None]:
    def ratio(metric: str) -> float | None:
        if len(participants) != 2:
            return None
        values = [float(metrics.get(metric) or 0) for metrics in participants.values()]
        if max(values, default=0.0) == 0.0:
            return 1.0
        return min(values) / max(values)

    return {
        "message_reciprocity": ratio("message_count"),
        "word_reciprocity": ratio("word_count"),
        "turn_reciprocity": ratio("turn_count"),
        "initiation_reciprocity": ratio("initiations"),
    }


def _participant_metrics(
    messages: Sequence[AnalyticMessage],
    turns: Sequence[Turn],
    responses: Sequence[ResponseSample],
    config: AnalyticsConfig,
) -> dict[int, dict[str, object]]:
    participants = sorted({m.participant_id for m in messages if m.participant_id is not None})
    message_count = Counter(m.participant_id for m in messages if m.participant_id is not None)
    word_count = Counter()
    char_count = Counter()
    questions = Counter()
    exclamations = Counter()
    affection = Counter()
    negative = Counter()
    active_days: dict[int, set[object]] = defaultdict(set)
    for message in messages:
        if message.participant_id is None:
            continue
        pid = message.participant_id
        word_count[pid] += message.word_count
        char_count[pid] += message.character_count
        questions[pid] += message.question_mark_count
        exclamations[pid] += message.exclamation_mark_count
        affection[pid] += _marker_hits(message.text_clean, config.affection_markers)
        negative[pid] += _marker_hits(message.text_clean, config.negative_markers)
        if message.period_date is not None:
            active_days[pid].add(message.period_date)
        elif message.timestamp_us is not None:
            active_days[pid].add(message.timestamp_us // 86_400_000_000)

    turn_count = Counter(t.participant_id for t in turns if t.participant_id is not None)
    initiations = Counter()
    seen_sessions: set[int] = set()
    known_initiated_sessions = 0
    for turn in turns:
        if turn.session_id in seen_sessions:
            continue
        seen_sessions.add(turn.session_id)
        if turn.participant_id is not None:
            initiations[turn.participant_id] += 1
            known_initiated_sessions += 1

    latency_by_responder: dict[int, list[float]] = defaultdict(list)
    effort_by_responder: dict[int, list[float]] = defaultdict(list)
    for sample in responses:
        if sample.latency_seconds is not None:
            latency_by_responder[sample.responder_id].append(sample.latency_seconds)
        effort_by_responder[sample.responder_id].append(sample.response_effort_ratio)

    known_turns = max(1, sum(turn_count.values()))
    result: dict[int, dict[str, object]] = {}
    for pid in participants:
        response_values = latency_by_responder.get(pid, [])
        effort_values = effort_by_responder.get(pid, [])
        median_latency = median(response_values) if response_values else None
        median_effort = median(effort_values) if effort_values else None
        activity_share = turn_count[pid] / known_turns
        initiation_share = initiations[pid] / max(1, known_initiated_sessions)
        responsiveness = (
            1.0 - _clamp(median_latency / config.responsiveness_reference_seconds)
            if median_latency is not None
            else 0.0
        )
        question_rate = _clamp(questions[pid] / max(1, turn_count[pid]))
        affection_rate = _clamp(affection[pid] / max(1, turn_count[pid]))
        engagement = 100 * (
            config.engagement_activity_weight * activity_share
            + config.engagement_initiation_weight * initiation_share
            + config.engagement_responsiveness_weight * responsiveness
            + config.engagement_question_weight * question_rate
            + config.engagement_affection_weight * affection_rate
        )
        result[pid] = {
            "message_count": message_count[pid],
            "word_count": word_count[pid],
            "character_count": char_count[pid],
            "active_days": len(active_days[pid]),
            "turn_count": turn_count[pid],
            "initiations": initiations[pid],
            "initiation_share": initiation_share,
            "question_count": questions[pid],
            "exclamation_count": exclamations[pid],
            "affection_marker_count": affection[pid],
            "negative_marker_count": negative[pid],
            "median_response_latency_seconds": median_latency,
            "median_response_effort_ratio": median_effort,
            "engagement_score": round(engagement, 6),
        }
    return result


def _conflict_candidates(
    turns: Sequence[Turn], config: AnalyticsConfig
) -> list[ConflictCandidate]:
    grouped: dict[int, list[Turn]] = defaultdict(list)
    for turn in turns:
        grouped[turn.session_id].append(turn)
    session_ids = sorted(grouped, key=lambda sid: grouped[sid][0].turn_id)
    candidates: list[ConflictCandidate] = []

    for index, session_id in enumerate(session_ids):
        session_turns = grouped[session_id]
        text = "\n".join(turn.text for turn in session_turns)
        negative_factor = _clamp(
            _marker_hits(text, config.negative_markers) / max(1, len(session_turns))
        )
        exclamation_factor = _clamp(
            sum(turn.exclamation_mark_count for turn in session_turns)
            / max(1, 2 * len(session_turns))
        )
        transitions = list(zip(session_turns, session_turns[1:]))
        valid_transition_count = 0
        rapid_count = 0
        for left, right in transitions:
            if left.end_us is None or right.start_us is None:
                continue
            gap_seconds = (right.start_us - left.end_us) / 1_000_000
            if gap_seconds < 0:
                continue
            valid_transition_count += 1
            if gap_seconds <= config.rapid_exchange_seconds:
                rapid_count += 1
        rapid_factor = rapid_count / valid_transition_count if valid_transition_count else 0.0

        current_end = session_turns[-1].end_us
        next_start = None
        if index + 1 < len(session_ids):
            next_start = grouped[session_ids[index + 1]][0].start_us
        if current_end is not None and next_start is not None and next_start >= current_end:
            silence_seconds = (next_start - current_end) / 1_000_000
            post_silence_factor = _clamp(
                silence_seconds / config.post_silence_reference_seconds
            )
        else:
            post_silence_factor = 0.0

        score = (
            config.conflict_negative_weight * negative_factor
            + config.conflict_rapid_weight * rapid_factor
            + config.conflict_exclamation_weight * exclamation_factor
            + config.conflict_post_silence_weight * post_silence_factor
        )
        if score < config.conflict_threshold:
            continue
        candidates.append(
            ConflictCandidate(
                conversation_id=session_turns[0].conversation_id,
                session_id=session_id,
                score=round(score, 6),
                start_us=session_turns[0].start_us,
                end_us=session_turns[-1].end_us,
                factors={
                    "negative": round(negative_factor, 6),
                    "rapid_exchange": round(rapid_factor, 6),
                    "exclamation": round(exclamation_factor, 6),
                    "post_silence": round(post_silence_factor, 6),
                },
                source_message_ids=tuple(
                    message_id for turn in session_turns for message_id in turn.message_ids
                ),
            )
        )
    return candidates


def analyze_conversation(
    messages: Iterable[AnalyticMessage], config: AnalyticsConfig | None = None
) -> ConversationAnalytics:
    cfg = config or AnalyticsConfig()
    source = list(messages)
    if not source:
        raise ValueError("analyze_conversation requires at least one message")
    conversation_ids = {m.conversation_id for m in source}
    if len(conversation_ids) != 1:
        raise ValueError("analyze_conversation expects exactly one conversation")

    turns = build_turns(source)
    responses = response_samples(turns)
    metrics = _participant_metrics(source, turns, responses, cfg)
    conflicts = _conflict_candidates(turns, cfg)
    daily_metrics = build_daily_metrics(source, turns, responses, cfg)
    change_points = detect_change_points(
        daily_metrics,
        baseline_window_days=cfg.change_baseline_window_days,
        min_baseline_days=cfg.change_min_baseline_days,
        z_threshold=cfg.change_z_threshold,
    )
    session_count = len({message.session_id for message in source})
    unknown_sender_count = sum(message.participant_id is None for message in source)
    message_ids = [message.message_id for message in source]

    return ConversationAnalytics(
        conversation_id=source[0].conversation_id,
        source_message_count=len(source),
        known_sender_message_count=len(source) - unknown_sender_count,
        unknown_sender_message_count=unknown_sender_count,
        turn_count=len(turns),
        session_count=session_count,
        participant_metrics=metrics,
        reciprocity=_reciprocity(metrics),
        response_samples=responses,
        conflicts=conflicts,
        daily_metrics=daily_metrics,
        change_points=change_points,
        turns=turns,
        diagnostics={
            "duplicate_message_ids": sorted(
                message_id for message_id, count in Counter(message_ids).items() if count > 1
            ),
            "message_accounting_ok": len(message_ids) == len(source),
            "uses_a3_session_boundaries": True,
        },
    )
