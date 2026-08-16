from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AnalyticMessage:
    """A4 read model built from A2 analysis_messages + A3 processed_message."""

    message_id: int
    conversation_id: int
    participant_id: int | None
    timestamp_us: int | None
    text_clean: str
    session_id: int
    sequence_number: int
    word_count: int
    character_count: int
    question_mark_count: int
    exclamation_mark_count: int
    has_attachment: bool = False
    utc_date: str | None = None
    local_date: str | None = None
    utc_weekday: int | None = None
    utc_hour: int | None = None
    local_weekday: int | None = None
    local_hour: int | None = None

    @property
    def period_date(self) -> str | None:
        return self.local_date or self.utc_date

    @property
    def period_basis(self) -> str | None:
        if self.local_date is not None:
            return "local"
        if self.utc_date is not None:
            return "utc"
        return None


@dataclass(frozen=True, slots=True)
class Turn:
    turn_id: int
    conversation_id: int
    session_id: int
    participant_id: int | None
    start_us: int | None
    end_us: int | None
    message_ids: tuple[int, ...]
    message_count: int
    word_count: int
    character_count: int
    question_mark_count: int
    exclamation_mark_count: int
    text: str


@dataclass(frozen=True, slots=True)
class ResponseSample:
    conversation_id: int
    session_id: int
    from_participant_id: int
    responder_id: int
    previous_turn_id: int
    response_turn_id: int
    latency_seconds: float | None
    response_effort_ratio: float


@dataclass(frozen=True, slots=True)
class ConflictCandidate:
    conversation_id: int
    session_id: int
    score: float
    start_us: int | None
    end_us: int | None
    factors: dict[str, float]
    source_message_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class SilenceEvent:
    conversation_id: int
    previous_session_id: int
    next_session_id: int
    gap_seconds: float
    previous_turn_id: int
    return_turn_id: int
    before_participant_id: int | None
    return_participant_id: int | None
    source_message_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class TimeBucketMetric:
    conversation_id: int
    participant_id: int
    time_basis: str
    bucket_kind: str
    bucket_value: str
    message_count: int
    source_message_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class DailyParticipantMetric:
    conversation_id: int
    participant_id: int
    period_date: str
    date_basis: str
    message_count: int
    word_count: int
    turn_count: int
    initiations: int
    question_count: int
    affection_marker_count: int
    negative_marker_count: int
    median_response_latency_seconds: float | None
    median_response_effort_ratio: float | None
    source_message_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ChangePoint:
    conversation_id: int
    participant_id: int
    metric: str
    period_date: str
    value: float
    baseline_median: float
    robust_z_score: float
    direction: str
    source_message_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PeriodParticipantMetric:
    conversation_id: int
    participant_id: int
    period_kind: str
    period_start: str
    period_end: str
    date_basis: str
    message_count: int
    word_count: int
    turn_count: int
    initiations: int
    question_count: int
    affection_marker_count: int
    negative_marker_count: int
    median_response_latency_seconds: float | None
    median_response_effort_ratio: float | None
    source_message_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class EngagementPeriodSignal:
    conversation_id: int
    participant_id: int
    period_start: str
    period_end: str
    score: float
    direction: str
    component_scores: dict[str, float]
    source_message_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class DyadicRegime:
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


@dataclass(frozen=True, slots=True)
class TrendSummary:
    conversation_id: int
    participant_id: int
    period_kind: str
    metric: str
    window_periods: int
    period_start: str
    period_end: str
    first_value: float
    last_value: float
    slope_per_period: float
    normalized_slope: float
    percent_change: float | None
    direction: str
    source_message_ids: tuple[int, ...]


@dataclass(slots=True)
class ConversationAnalytics:
    conversation_id: int
    source_message_count: int
    known_sender_message_count: int
    unknown_sender_message_count: int
    turn_count: int
    session_count: int
    source_fingerprint: str = ""
    participant_metrics: dict[int, dict[str, Any]] = field(default_factory=dict)
    reciprocity: dict[str, float | None] = field(default_factory=dict)
    response_samples: list[ResponseSample] = field(default_factory=list)
    conflicts: list[ConflictCandidate] = field(default_factory=list)
    silence_events: list[SilenceEvent] = field(default_factory=list)
    time_buckets: list[TimeBucketMetric] = field(default_factory=list)
    daily_metrics: list[DailyParticipantMetric] = field(default_factory=list)
    change_points: list[ChangePoint] = field(default_factory=list)
    period_metrics: list[PeriodParticipantMetric] = field(default_factory=list)
    engagement_signals: list[EngagementPeriodSignal] = field(default_factory=list)
    dyadic_regimes: list[DyadicRegime] = field(default_factory=list)
    trend_summaries: list[TrendSummary] = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
