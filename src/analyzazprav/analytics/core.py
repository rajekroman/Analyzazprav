from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median
from typing import Iterable, Sequence

from .config import AnalyticsConfig
from .models import ConflictCandidate, LatencySample, Message, Session, Turn


def _word_count(text: str) -> int:
    return len(text.split()) if text else 0


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _marker_hits(text: str, markers: Sequence[str]) -> int:
    lowered = text.casefold()
    return sum(lowered.count(marker.casefold()) for marker in markers if marker)


def build_turns(messages: Iterable[Message], config: AnalyticsConfig) -> list[Turn]:
    ordered = sorted(messages, key=lambda m: (m.timestamp, m.message_id))
    if not ordered:
        return []

    conversation_ids = {m.conversation_id for m in ordered}
    if len(conversation_ids) != 1:
        raise ValueError("build_turns expects messages from exactly one conversation")

    turns: list[Turn] = []
    batch: list[Message] = []

    def flush() -> None:
        if not batch:
            return
        idx = len(turns) + 1
        joined = "\n".join(m.text for m in batch if m.text)
        turns.append(
            Turn(
                turn_id=f"turn-{idx:08d}",
                conversation_id=batch[0].conversation_id,
                participant_id=batch[0].participant_id,
                start_at=batch[0].timestamp,
                end_at=batch[-1].timestamp,
                message_ids=tuple(m.message_id for m in batch),
                message_count=len(batch),
                word_count=sum(_word_count(m.text) for m in batch),
                character_count=sum(len(m.text) for m in batch),
                text=joined,
            )
        )

    for message in ordered:
        if not batch:
            batch.append(message)
            continue
        gap = (message.timestamp - batch[-1].timestamp).total_seconds()
        same_sender = message.participant_id == batch[-1].participant_id
        if same_sender and 0 <= gap <= config.turn_gap_seconds:
            batch.append(message)
        else:
            flush()
            batch = [message]
    flush()
    return turns


def build_sessions(turns: Sequence[Turn], config: AnalyticsConfig) -> list[Session]:
    if not turns:
        return []
    sessions: list[Session] = []
    batch: list[Turn] = []

    def flush() -> None:
        if not batch:
            return
        idx = len(sessions) + 1
        sessions.append(
            Session(
                session_id=f"session-{idx:08d}",
                conversation_id=batch[0].conversation_id,
                start_at=batch[0].start_at,
                end_at=batch[-1].end_at,
                initiator_id=batch[0].participant_id,
                turn_ids=tuple(t.turn_id for t in batch),
            )
        )

    for turn in turns:
        if not batch:
            batch.append(turn)
            continue
        gap = (turn.start_at - batch[-1].end_at).total_seconds()
        if 0 <= gap < config.session_gap_seconds:
            batch.append(turn)
        else:
            flush()
            batch = [turn]
    flush()
    return sessions


def response_latencies(
    turns: Sequence[Turn], sessions: Sequence[Session]
) -> list[LatencySample]:
    by_id = {turn.turn_id: turn for turn in turns}
    samples: list[LatencySample] = []
    for session in sessions:
        session_turns = [by_id[turn_id] for turn_id in session.turn_ids]
        for previous, current in zip(session_turns, session_turns[1:]):
            if previous.participant_id == current.participant_id:
                continue
            latency = (current.start_at - previous.end_at).total_seconds()
            if latency < 0:
                raise ValueError("negative response latency detected")
            samples.append(
                LatencySample(
                    session_id=session.session_id,
                    from_participant_id=previous.participant_id,
                    responder_id=current.participant_id,
                    previous_turn_id=previous.turn_id,
                    response_turn_id=current.turn_id,
                    latency_seconds=latency,
                )
            )
    return samples


def participant_metrics(
    messages: Sequence[Message],
    turns: Sequence[Turn],
    sessions: Sequence[Session],
    latencies: Sequence[LatencySample],
    config: AnalyticsConfig,
) -> dict[str, dict[str, float | int | None]]:
    participants = sorted({message.participant_id for message in messages})
    message_counts = Counter(m.participant_id for m in messages)
    word_counts = Counter()
    char_counts = Counter()
    active_days: dict[str, set] = defaultdict(set)
    for message in messages:
        word_counts[message.participant_id] += _word_count(message.text)
        char_counts[message.participant_id] += len(message.text)
        active_days[message.participant_id].add(message.timestamp.date())

    turn_counts = Counter(t.participant_id for t in turns)
    initiations = Counter(s.initiator_id for s in sessions)
    questions = Counter()
    affection = Counter()
    negative = Counter()
    exclamations = Counter()
    for turn in turns:
        questions[turn.participant_id] += turn.text.count("?")
        affection[turn.participant_id] += _marker_hits(turn.text, config.affection_markers)
        negative[turn.participant_id] += _marker_hits(turn.text, config.negative_markers)
        exclamations[turn.participant_id] += turn.text.count("!")

    latency_by_responder: dict[str, list[float]] = defaultdict(list)
    for sample in latencies:
        latency_by_responder[sample.responder_id].append(sample.latency_seconds)

    total_turns = max(1, len(turns))
    total_sessions = max(1, len(sessions))
    result: dict[str, dict[str, float | int | None]] = {}
    for participant in participants:
        response_values = latency_by_responder.get(participant, [])
        median_latency = median(response_values) if response_values else None
        activity_share = turn_counts[participant] / total_turns
        initiation_share = initiations[participant] / total_sessions
        responsiveness = (
            1.0 - _clamp(median_latency / config.session_gap_seconds)
            if median_latency is not None
            else 0.0
        )
        question_rate = _clamp(questions[participant] / max(1, turn_counts[participant]))
        affection_rate = _clamp(affection[participant] / max(1, turn_counts[participant]))
        engagement = 100.0 * (
            config.engagement_activity_weight * activity_share
            + config.engagement_initiation_weight * initiation_share
            + config.engagement_responsiveness_weight * responsiveness
            + config.engagement_question_weight * question_rate
            + config.engagement_affection_weight * affection_rate
        )
        result[participant] = {
            "message_count": message_counts[participant],
            "word_count": word_counts[participant],
            "character_count": char_counts[participant],
            "active_days": len(active_days[participant]),
            "turn_count": turn_counts[participant],
            "initiations": initiations[participant],
            "initiation_share": initiation_share,
            "question_count": questions[participant],
            "affection_marker_count": affection[participant],
            "negative_marker_count": negative[participant],
            "exclamation_count": exclamations[participant],
            "median_response_latency_seconds": median_latency,
            "engagement_score": round(engagement, 3),
        }
    return result


def reciprocity_metrics(participants: dict[str, dict[str, float | int | None]]) -> dict[str, float]:
    def ratio(metric: str) -> float:
        values = [float(metrics.get(metric) or 0) for metrics in participants.values()]
        if not values or max(values) == 0:
            return 1.0
        if len(values) != 2:
            # Pairwise relationship metrics are intentionally conservative for group chats.
            return 0.0
        return min(values) / max(values)

    return {
        "message_reciprocity": round(ratio("message_count"), 6),
        "word_reciprocity": round(ratio("word_count"), 6),
        "turn_reciprocity": round(ratio("turn_count"), 6),
        "initiation_reciprocity": round(ratio("initiations"), 6),
    }


def conflict_candidates(
    turns: Sequence[Turn], sessions: Sequence[Session], config: AnalyticsConfig
) -> list[ConflictCandidate]:
    by_id = {turn.turn_id: turn for turn in turns}
    candidates: list[ConflictCandidate] = []
    for index, session in enumerate(sessions):
        session_turns = [by_id[turn_id] for turn_id in session.turn_ids]
        if not session_turns:
            continue
        text = "\n".join(turn.text for turn in session_turns)
        negative_hits = _marker_hits(text, config.negative_markers)
        exclamation_hits = text.count("!")
        negative_factor = _clamp(negative_hits / max(1, len(session_turns)))
        exclamation_factor = _clamp(exclamation_hits / max(1, 2 * len(session_turns)))

        transitions = list(zip(session_turns, session_turns[1:]))
        if transitions:
            rapid = sum(
                1
                for left, right in transitions
                if 0 <= (right.start_at - left.end_at).total_seconds() <= config.rapid_exchange_seconds
            )
            rapid_factor = rapid / len(transitions)
        else:
            rapid_factor = 0.0

        if index + 1 < len(sessions):
            next_session = sessions[index + 1]
            silence = (next_session.start_at - session.end_at).total_seconds()
            post_silence_factor = _clamp(silence / (3 * config.session_gap_seconds))
        else:
            post_silence_factor = 0.0

        score = (
            config.conflict_negative_weight * negative_factor
            + config.conflict_rapid_weight * rapid_factor
            + config.conflict_exclamation_weight * exclamation_factor
            + config.conflict_post_silence_weight * post_silence_factor
        )
        if score >= config.conflict_threshold:
            candidates.append(
                ConflictCandidate(
                    session_id=session.session_id,
                    score=round(score, 6),
                    start_at=session.start_at,
                    end_at=session.end_at,
                    factors={
                        "negative": round(negative_factor, 6),
                        "rapid_exchange": round(rapid_factor, 6),
                        "exclamation": round(exclamation_factor, 6),
                        "post_silence": round(post_silence_factor, 6),
                    },
                    source_message_ids=tuple(
                        message_id
                        for turn in session_turns
                        for message_id in turn.message_ids
                    ),
                )
            )
    return candidates
