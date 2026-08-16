from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import median, pstdev
from typing import Sequence

from .config import AnalyticsConfig
from .models import AnalyticMessage, ChangePoint, DailyParticipantMetric, ResponseSample, Turn


def _marker_hits(text: str, markers: Sequence[str]) -> int:
    lowered = text.casefold()
    return sum(lowered.count(marker.casefold()) for marker in markers if marker)


def _date_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def build_daily_metrics(
    messages: Sequence[AnalyticMessage],
    turns: Sequence[Turn],
    responses: Sequence[ResponseSample],
    config: AnalyticsConfig,
) -> list[DailyParticipantMetric]:
    """Build a gap-free daily series per participant.

    A3-local calendar dates are authoritative when present. UTC is only a
    fallback for records where A3 could not derive a local calendar date.
    Zero-activity days are retained because they are analytically meaningful.
    """

    dated_messages = [m for m in messages if m.participant_id is not None and m.period_date]
    if not dated_messages:
        return []

    participants = sorted({int(m.participant_id) for m in dated_messages if m.participant_id is not None})
    known_dates = [date.fromisoformat(str(m.period_date)) for m in dated_messages]
    start_date, end_date = min(known_dates), max(known_dates)

    by_key: dict[tuple[int, str], list[AnalyticMessage]] = defaultdict(list)
    participant_basis: dict[int, str] = {}
    message_by_id = {m.message_id: m for m in messages}
    for message in dated_messages:
        pid = int(message.participant_id)
        by_key[(pid, str(message.period_date))].append(message)
        if message.local_date is not None:
            participant_basis[pid] = "local"
        else:
            participant_basis.setdefault(pid, "utc")

    turn_date: dict[int, str] = {}
    for turn in turns:
        if turn.participant_id is None or not turn.message_ids:
            continue
        first = message_by_id.get(turn.message_ids[0])
        if first and first.period_date:
            turn_date[turn.turn_id] = first.period_date

    turn_counts: Counter[tuple[int, str]] = Counter()
    initiations: Counter[tuple[int, str]] = Counter()
    seen_sessions: set[int] = set()
    for turn in turns:
        if turn.participant_id is None:
            continue
        period = turn_date.get(turn.turn_id)
        if period:
            turn_counts[(turn.participant_id, period)] += 1
        if turn.session_id not in seen_sessions:
            seen_sessions.add(turn.session_id)
            if period:
                initiations[(turn.participant_id, period)] += 1

    latency_by_key: dict[tuple[int, str], list[float]] = defaultdict(list)
    effort_by_key: dict[tuple[int, str], list[float]] = defaultdict(list)
    response_turn_date = {turn.turn_id: turn_date.get(turn.turn_id) for turn in turns}
    for sample in responses:
        period = response_turn_date.get(sample.response_turn_id)
        if not period:
            continue
        key = (sample.responder_id, period)
        if sample.latency_seconds is not None:
            latency_by_key[key].append(sample.latency_seconds)
        effort_by_key[key].append(sample.response_effort_ratio)

    rows: list[DailyParticipantMetric] = []
    for participant_id in participants:
        for day in _date_range(start_date, end_date):
            period = day.isoformat()
            source = by_key.get((participant_id, period), [])
            if source:
                basis = "local" if any(m.local_date == period for m in source) else "utc"
            else:
                basis = participant_basis.get(participant_id, "utc")
            latency_values = latency_by_key.get((participant_id, period), [])
            effort_values = effort_by_key.get((participant_id, period), [])
            rows.append(
                DailyParticipantMetric(
                    conversation_id=messages[0].conversation_id,
                    participant_id=participant_id,
                    period_date=period,
                    date_basis=basis,
                    message_count=len(source),
                    word_count=sum(m.word_count for m in source),
                    turn_count=turn_counts[(participant_id, period)],
                    initiations=initiations[(participant_id, period)],
                    question_count=sum(m.question_mark_count for m in source),
                    affection_marker_count=sum(
                        _marker_hits(m.text_clean, config.affection_markers) for m in source
                    ),
                    negative_marker_count=sum(
                        _marker_hits(m.text_clean, config.negative_markers) for m in source
                    ),
                    median_response_latency_seconds=(
                        median(latency_values) if latency_values else None
                    ),
                    median_response_effort_ratio=(
                        median(effort_values) if effort_values else None
                    ),
                    source_message_ids=tuple(m.message_id for m in source),
                )
            )
    return rows


def _robust_z(value: float, baseline: Sequence[float]) -> tuple[float, float]:
    center = median(baseline)
    deviations = [abs(item - center) for item in baseline]
    mad = median(deviations)
    if mad > 0:
        return center, 0.67448975 * (value - center) / mad
    spread = pstdev(baseline)
    if spread > 0:
        return center, (value - center) / spread
    # A perfectly stable baseline still needs to flag a genuine departure.
    # The scale of 1 keeps count-like metrics interpretable and finite.
    return center, (value - center) / max(1.0, abs(center) * 0.1)


def detect_change_points(
    daily_metrics: Sequence[DailyParticipantMetric],
    *,
    baseline_window_days: int = 28,
    min_baseline_days: int = 7,
    z_threshold: float = 2.5,
) -> list[ChangePoint]:
    """Detect candidate daily departures from each participant's own baseline."""

    if baseline_window_days < min_baseline_days or min_baseline_days < 2:
        raise ValueError("invalid baseline window configuration")
    if z_threshold <= 0:
        raise ValueError("z_threshold must be positive")

    metrics = (
        "message_count",
        "word_count",
        "turn_count",
        "initiations",
        "question_count",
        "affection_marker_count",
        "negative_marker_count",
        "median_response_latency_seconds",
        "median_response_effort_ratio",
    )
    grouped: dict[int, list[DailyParticipantMetric]] = defaultdict(list)
    for row in daily_metrics:
        grouped[row.participant_id].append(row)
    changes: list[ChangePoint] = []

    for participant_id, rows in grouped.items():
        rows.sort(key=lambda row: row.period_date)
        for metric in metrics:
            history: list[float] = []
            for row in rows:
                raw_value = getattr(row, metric)
                if raw_value is None:
                    continue
                value = float(raw_value)
                baseline = history[-baseline_window_days:]
                if len(baseline) >= min_baseline_days:
                    center, z_score = _robust_z(value, baseline)
                    if abs(z_score) >= z_threshold:
                        changes.append(
                            ChangePoint(
                                conversation_id=row.conversation_id,
                                participant_id=participant_id,
                                metric=metric,
                                period_date=row.period_date,
                                value=value,
                                baseline_median=center,
                                robust_z_score=round(z_score, 6),
                                direction="increasing" if z_score > 0 else "decreasing",
                                source_message_ids=row.source_message_ids,
                            )
                        )
                history.append(value)
    return changes
