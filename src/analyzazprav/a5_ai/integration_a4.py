from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Iterable, Protocol, Sequence

from .models import AnalysisCandidate, MessageRecord

UTC = timezone.utc


def _ids(values: Iterable[object]) -> tuple[str, ...]:
    return tuple(str(value) for value in values)


def _utc_us(value: int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(int(value) / 1_000_000, tz=UTC)


def _date_start(value: str) -> datetime:
    return datetime.combine(datetime.fromisoformat(value).date(), time.min, tzinfo=UTC)


def _date_end(value: str) -> datetime:
    return datetime.combine(datetime.fromisoformat(value).date(), time.max, tzinfo=UTC)


class A4ConflictLike(Protocol):
    conversation_id: int
    session_id: int
    score: float
    start_us: int | None
    end_us: int | None
    factors: dict[str, float]
    source_message_ids: tuple[int, ...]


class A4ChangePointLike(Protocol):
    conversation_id: int
    participant_id: int
    metric: str
    period_date: str
    value: float
    baseline_median: float
    robust_z_score: float
    direction: str
    source_message_ids: tuple[int, ...]


class A4EngagementSignalLike(Protocol):
    conversation_id: int
    participant_id: int
    period_start: str
    period_end: str
    score: float
    direction: str
    component_scores: dict[str, float]
    source_message_ids: tuple[int, ...]


class A4DyadicRegimeLike(Protocol):
    conversation_id: int
    period_start: str
    period_end: str
    participant_a_id: int
    participant_a_direction: str
    participant_a_score: float
    participant_b_id: int
    participant_b_direction: str
    participant_b_score: float
    regime_type: str
    source_message_ids: tuple[int, ...]


class A4TopicCandidateLike(Protocol):
    conversation_id: int
    topic_key: str
    method: str
    normalized_phrase: str
    ngram_size: int
    document_frequency: int
    document_frequency_ratio: float
    occurrence_count: int
    participant_count: int
    salience: float
    first_period_date: str | None
    last_period_date: str | None
    source_message_ids: tuple[int, ...]


class A4AnalyticMessageLike(Protocol):
    message_id: int
    conversation_id: int
    participant_id: int | None
    timestamp_us: int | None
    text_clean: str
    has_attachment: bool


def candidate_from_a4_conflict(conflict: A4ConflictLike, *, manual_request: bool = False) -> AnalysisCandidate:
    score = float(conflict.score)
    if not 0.0 <= score <= 1.0:
        raise ValueError("A4 conflict score must be between 0 and 1")
    start = _utc_us(conflict.start_us)
    end = _utc_us(conflict.end_us)
    if start is None or end is None:
        raise ValueError("A4 conflict candidate requires known start/end timestamps")
    factors = {str(name): float(value) for name, value in conflict.factors.items()}
    return AnalysisCandidate(
        id=f"a4-conflict:{conflict.conversation_id}:{conflict.session_id}",
        conversation_id=str(conflict.conversation_id),
        start_ts=start,
        end_ts=end,
        candidate_type="conflict",
        importance_score=round(score * 100.0, 6),
        metrics_during={"conflict_score": score, **factors},
        detected_signals=tuple(name for name, value in factors.items() if value > 0.0),
        evidence_message_ids=_ids(conflict.source_message_ids),
        manual_request=manual_request,
        metadata={"source": "a4", "session_id": conflict.session_id},
    )


def candidate_from_a4_change_point(change: A4ChangePointLike) -> AnalysisCandidate:
    z = float(change.robust_z_score)
    importance = min(100.0, max(60.0, abs(z) * 20.0))
    return AnalysisCandidate(
        id=f"a4-change:{change.conversation_id}:{change.participant_id}:{change.metric}:{change.period_date}",
        conversation_id=str(change.conversation_id),
        start_ts=_date_start(change.period_date),
        end_ts=_date_end(change.period_date),
        candidate_type="change_point",
        importance_score=round(importance, 6),
        metrics_during={"value": float(change.value), "baseline_median": float(change.baseline_median), "robust_z_score": z},
        detected_signals=(f"{change.metric}:{change.direction}",),
        evidence_message_ids=_ids(change.source_message_ids),
        metadata={"source": "a4", "participant_id": change.participant_id, "metric": change.metric, "direction": change.direction},
    )


def candidate_from_a4_engagement(signal: A4EngagementSignalLike) -> AnalysisCandidate:
    return AnalysisCandidate(
        id=f"a4-engagement:{signal.conversation_id}:{signal.participant_id}:{signal.period_start}",
        conversation_id=str(signal.conversation_id),
        start_ts=_date_start(signal.period_start),
        end_ts=_date_end(signal.period_end),
        candidate_type="engagement_signal",
        importance_score=80.0 if signal.direction != "stable" else 50.0,
        metrics_during={"engagement_signal_score": float(signal.score), **{str(k): float(v) for k, v in signal.component_scores.items()}},
        detected_signals=(f"engagement:{signal.direction}",),
        evidence_message_ids=_ids(signal.source_message_ids),
        metadata={"source": "a4", "participant_id": signal.participant_id, "direction": signal.direction},
    )


def candidate_from_a4_regime(regime: A4DyadicRegimeLike) -> AnalysisCandidate:
    return AnalysisCandidate(
        id=f"a4-regime:{regime.conversation_id}:{regime.period_start}",
        conversation_id=str(regime.conversation_id),
        start_ts=_date_start(regime.period_start),
        end_ts=_date_end(regime.period_end),
        candidate_type="dyadic_regime",
        importance_score=85.0 if regime.regime_type != "stable_or_mixed" else 50.0,
        metrics_during={"participant_a_score": float(regime.participant_a_score), "participant_b_score": float(regime.participant_b_score)},
        detected_signals=(regime.regime_type,),
        evidence_message_ids=_ids(regime.source_message_ids),
        metadata={
            "source": "a4",
            "participant_a_id": regime.participant_a_id,
            "participant_a_direction": regime.participant_a_direction,
            "participant_b_id": regime.participant_b_id,
            "participant_b_direction": regime.participant_b_direction,
            "regime_type": regime.regime_type,
        },
    )


def candidate_from_a4_topic(topic: A4TopicCandidateLike) -> AnalysisCandidate:
    if not topic.first_period_date or not topic.last_period_date:
        raise ValueError("A4 topic candidate requires dated evidence for A5 period analysis")
    return AnalysisCandidate(
        id=f"a4-topic:{topic.conversation_id}:{topic.topic_key}",
        conversation_id=str(topic.conversation_id),
        start_ts=_date_start(topic.first_period_date),
        end_ts=_date_end(topic.last_period_date),
        candidate_type="lexical_topic",
        importance_score=65.0,
        metrics_during={
            "document_frequency": float(topic.document_frequency),
            "document_frequency_ratio": float(topic.document_frequency_ratio),
            "occurrence_count": float(topic.occurrence_count),
            "participant_count": float(topic.participant_count),
            "salience": float(topic.salience),
        },
        detected_signals=("lexical_topic_candidate",),
        evidence_message_ids=_ids(topic.source_message_ids),
        metadata={"source": "a4", "topic_key": topic.topic_key, "method": topic.method, "normalized_phrase": topic.normalized_phrase, "ngram_size": topic.ngram_size},
    )


def message_from_a4(message: A4AnalyticMessageLike) -> MessageRecord:
    timestamp = _utc_us(message.timestamp_us)
    if timestamp is None:
        raise ValueError("A4 message requires known timestamp for A5 context")
    return MessageRecord(
        id=str(message.message_id),
        conversation_id=str(message.conversation_id),
        participant_id=str(message.participant_id) if message.participant_id is not None else "unknown",
        timestamp=timestamp,
        text=message.text_clean or "",
        attachment_types=("attachment",) if bool(message.has_attachment) else (),
    )


@dataclass
class A4MessageSource:
    messages: tuple[MessageRecord, ...]

    @classmethod
    def from_a4_messages(cls, messages: Iterable[A4AnalyticMessageLike]) -> "A4MessageSource":
        return cls(tuple(message_from_a4(message) for message in messages if message.timestamp_us is not None))

    def list_messages(self, conversation_id: str, start_ts: datetime, end_ts: datetime) -> Sequence[MessageRecord]:
        return [m for m in self.messages if m.conversation_id == str(conversation_id) and start_ts <= m.timestamp <= end_ts]
