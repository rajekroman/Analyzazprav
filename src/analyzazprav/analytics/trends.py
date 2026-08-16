from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import median, pstdev
from typing import Sequence

from .config import AnalyticsConfig
from .models import (
    AnalyticMessage,
    ChangePoint,
    DailyParticipantMetric,
    DyadicRegime,
    EngagementPeriodSignal,
    PeriodParticipantMetric,
    ResponseSample,
    Turn,
)


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


def _period_bounds(day: date, period_kind: str) -> tuple[date, date]:
    if period_kind == "week":
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=6)
    if period_kind == "month":
        start = day.replace(day=1)
        if start.month == 12:
            next_start = start.replace(year=start.year + 1, month=1)
        else:
            next_start = start.replace(month=start.month + 1)
        return start, next_start - timedelta(days=1)
    raise ValueError("period_kind must be 'week' or 'month'")


def _next_period(start: date, period_kind: str) -> date:
    if period_kind == "week":
        return start + timedelta(days=7)
    if start.month == 12:
        return start.replace(year=start.year + 1, month=1)
    return start.replace(month=start.month + 1)


def build_period_metrics(
    messages: Sequence[AnalyticMessage],
    turns: Sequence[Turn],
    responses: Sequence[ResponseSample],
    config: AnalyticsConfig,
    *,
    period_kind: str,
) -> list[PeriodParticipantMetric]:
    """Aggregate raw A4 evidence into gap-free weekly or monthly periods."""

    dated_messages = [m for m in messages if m.participant_id is not None and m.period_date]
    if not dated_messages:
        return []
    participants = sorted({int(m.participant_id) for m in dated_messages if m.participant_id is not None})
    dates = [date.fromisoformat(str(m.period_date)) for m in dated_messages]
    first_start, _ = _period_bounds(min(dates), period_kind)
    last_start, _ = _period_bounds(max(dates), period_kind)

    period_starts: list[date] = []
    current = first_start
    while current <= last_start:
        period_starts.append(current)
        current = _next_period(current, period_kind)

    by_key: dict[tuple[int, str], list[AnalyticMessage]] = defaultdict(list)
    participant_basis: dict[int, str] = {}
    message_by_id = {m.message_id: m for m in messages}
    message_period: dict[int, str] = {}
    for message in dated_messages:
        pid = int(message.participant_id)
        day = date.fromisoformat(str(message.period_date))
        start, _ = _period_bounds(day, period_kind)
        start_text = start.isoformat()
        by_key[(pid, start_text)].append(message)
        message_period[message.message_id] = start_text
        if message.local_date is not None:
            participant_basis[pid] = "local"
        else:
            participant_basis.setdefault(pid, "utc")

    turn_period: dict[int, str] = {}
    for turn in turns:
        if turn.participant_id is None or not turn.message_ids:
            continue
        first = message_by_id.get(turn.message_ids[0])
        if first is not None and first.period_date:
            start, _ = _period_bounds(date.fromisoformat(first.period_date), period_kind)
            turn_period[turn.turn_id] = start.isoformat()

    turn_counts: Counter[tuple[int, str]] = Counter()
    initiations: Counter[tuple[int, str]] = Counter()
    seen_sessions: set[int] = set()
    for turn in turns:
        if turn.participant_id is None:
            continue
        period = turn_period.get(turn.turn_id)
        if period:
            turn_counts[(turn.participant_id, period)] += 1
        if turn.session_id not in seen_sessions:
            seen_sessions.add(turn.session_id)
            if period:
                initiations[(turn.participant_id, period)] += 1

    latency_by_key: dict[tuple[int, str], list[float]] = defaultdict(list)
    effort_by_key: dict[tuple[int, str], list[float]] = defaultdict(list)
    for sample in responses:
        period = turn_period.get(sample.response_turn_id)
        if not period:
            continue
        key = (sample.responder_id, period)
        if sample.latency_seconds is not None:
            latency_by_key[key].append(sample.latency_seconds)
        effort_by_key[key].append(sample.response_effort_ratio)

    rows: list[PeriodParticipantMetric] = []
    for participant_id in participants:
        for start in period_starts:
            start_text = start.isoformat()
            _, end = _period_bounds(start, period_kind)
            source = by_key.get((participant_id, start_text), [])
            if source:
                basis = "local" if any(m.local_date is not None for m in source) else "utc"
            else:
                basis = participant_basis.get(participant_id, "utc")
            latency_values = latency_by_key.get((participant_id, start_text), [])
            effort_values = effort_by_key.get((participant_id, start_text), [])
            rows.append(
                PeriodParticipantMetric(
                    conversation_id=messages[0].conversation_id,
                    participant_id=participant_id,
                    period_kind=period_kind,
                    period_start=start_text,
                    period_end=end.isoformat(),
                    date_basis=basis,
                    message_count=len(source),
                    word_count=sum(m.word_count for m in source),
                    turn_count=turn_counts[(participant_id, start_text)],
                    initiations=initiations[(participant_id, start_text)],
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


def build_engagement_signals(
    weekly_metrics: Sequence[PeriodParticipantMetric],
    config: AnalyticsConfig,
) -> list[EngagementPeriodSignal]:
    """Score weekly engagement change against each participant's own history."""

    components = (
        ("activity", "message_count", config.regime_activity_weight, 1.0),
        ("initiation", "initiations", config.regime_initiation_weight, 1.0),
        (
            "responsiveness",
            "median_response_latency_seconds",
            config.regime_responsiveness_weight,
            -1.0,
        ),
        ("effort", "median_response_effort_ratio", config.regime_effort_weight, 1.0),
        ("questions", "question_count", config.regime_question_weight, 1.0),
        ("affection", "affection_marker_count", config.regime_affection_weight, 1.0),
    )
    grouped: dict[int, list[PeriodParticipantMetric]] = defaultdict(list)
    for row in weekly_metrics:
        if row.period_kind == "week":
            grouped[row.participant_id].append(row)

    signals: list[EngagementPeriodSignal] = []
    for participant_id, rows in grouped.items():
        rows.sort(key=lambda row: row.period_start)
        histories: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            weighted_sum = 0.0
            eligible_weight = 0.0
            component_scores: dict[str, float] = {}
            for name, metric, weight, polarity in components:
                raw = getattr(row, metric)
                history = histories[metric]
                if raw is not None and len(history) >= config.regime_min_baseline_periods:
                    _, z_score = _robust_z(float(raw), history)
                    z_score *= polarity
                    z_score = max(-config.regime_z_clip, min(config.regime_z_clip, z_score))
                    normalized = 100.0 * z_score / config.regime_z_clip
                    component_scores[name] = round(normalized, 6)
                    weighted_sum += weight * normalized
                    eligible_weight += weight
                if raw is not None:
                    history.append(float(raw))

            if eligible_weight == 0:
                continue
            score = weighted_sum / eligible_weight
            if score >= config.regime_signal_threshold:
                direction = "increase"
            elif score <= -config.regime_signal_threshold:
                direction = "decrease"
            else:
                direction = "stable"
            signals.append(
                EngagementPeriodSignal(
                    conversation_id=row.conversation_id,
                    participant_id=participant_id,
                    period_start=row.period_start,
                    period_end=row.period_end,
                    score=round(score, 6),
                    direction=direction,
                    component_scores=component_scores,
                    source_message_ids=row.source_message_ids,
                )
            )
    return signals


def build_dyadic_regimes(
    signals: Sequence[EngagementPeriodSignal],
) -> list[DyadicRegime]:
    """Classify two-person weekly direction combinations without inferring motive."""

    grouped: dict[tuple[int, str], list[EngagementPeriodSignal]] = defaultdict(list)
    for signal in signals:
        grouped[(signal.conversation_id, signal.period_start)].append(signal)

    regimes: list[DyadicRegime] = []
    for (conversation_id, period_start), values in sorted(grouped.items()):
        if len(values) != 2:
            continue
        a, b = sorted(values, key=lambda item: item.participant_id)
        directions = {a.direction, b.direction}
        if a.direction == b.direction == "increase":
            regime_type = "mutual_approach"
        elif a.direction == b.direction == "decrease":
            regime_type = "mutual_withdrawal"
        elif directions == {"increase", "decrease"}:
            regime_type = "opposing_directions"
        elif "increase" in directions and "stable" in directions:
            regime_type = "one_sided_increase"
        elif "decrease" in directions and "stable" in directions:
            regime_type = "one_sided_decrease"
        else:
            regime_type = "stable_or_mixed"
        regimes.append(
            DyadicRegime(
                conversation_id=conversation_id,
                period_start=period_start,
                period_end=max(a.period_end, b.period_end),
                participant_a_id=a.participant_id,
                participant_a_direction=a.direction,
                participant_a_score=a.score,
                participant_b_id=b.participant_id,
                participant_b_direction=b.direction,
                participant_b_score=b.score,
                regime_type=regime_type,
                source_message_ids=tuple(sorted(set(a.source_message_ids + b.source_message_ids))),
            )
        )
    return regimes
