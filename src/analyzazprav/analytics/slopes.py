from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Sequence

from .config import AnalyticsConfig
from .models import PeriodParticipantMetric, TrendSummary


def build_trend_summaries(
    period_metrics: Sequence[PeriodParticipantMetric],
    config: AnalyticsConfig,
) -> list[TrendSummary]:
    """Summarize current weekly/monthly raw slopes over a bounded window.

    A positive latency slope means latency increased. The function deliberately
    does not relabel raw metric direction as a psychological interpretation.
    Missing observations are omitted rather than invented as zeroes.
    """

    metric_names = (
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
    grouped: dict[tuple[int, str], list[PeriodParticipantMetric]] = defaultdict(list)
    for row in period_metrics:
        grouped[(row.participant_id, row.period_kind)].append(row)

    summaries: list[TrendSummary] = []
    for (participant_id, period_kind), rows in sorted(grouped.items()):
        rows.sort(key=lambda row: row.period_start)
        window_size = (
            config.weekly_trend_window_periods
            if period_kind == "week"
            else config.monthly_trend_window_periods
        )
        window = rows[-window_size:]
        for metric in metric_names:
            points = [
                (index, row, float(value))
                for index, row in enumerate(window)
                if (value := getattr(row, metric)) is not None
            ]
            if len(points) < config.trend_min_periods:
                continue
            xs = [float(item[0]) for item in points]
            ys = [item[2] for item in points]
            x_center = sum(xs) / len(xs)
            y_center = sum(ys) / len(ys)
            denominator = sum((x - x_center) ** 2 for x in xs)
            if denominator == 0:
                continue
            slope = sum(
                (x - x_center) * (y - y_center) for x, y in zip(xs, ys)
            ) / denominator
            scale = max(1.0, median(abs(value) for value in ys))
            normalized_slope = slope / scale
            if normalized_slope >= config.trend_normalized_slope_threshold:
                direction = "increasing"
            elif normalized_slope <= -config.trend_normalized_slope_threshold:
                direction = "decreasing"
            else:
                direction = "stable"
            first_value = ys[0]
            last_value = ys[-1]
            percent_change = (
                (last_value - first_value) / abs(first_value)
                if first_value != 0
                else None
            )
            contributing_rows = [item[1] for item in points]
            source_ids = tuple(
                sorted(
                    {
                        message_id
                        for row in contributing_rows
                        for message_id in row.source_message_ids
                    }
                )
            )
            summaries.append(
                TrendSummary(
                    conversation_id=contributing_rows[0].conversation_id,
                    participant_id=participant_id,
                    period_kind=period_kind,
                    metric=metric,
                    window_periods=len(points),
                    period_start=contributing_rows[0].period_start,
                    period_end=contributing_rows[-1].period_end,
                    first_value=first_value,
                    last_value=last_value,
                    slope_per_period=round(slope, 9),
                    normalized_slope=round(normalized_slope, 9),
                    percent_change=(
                        None if percent_change is None else round(percent_change, 9)
                    ),
                    direction=direction,
                    source_message_ids=source_ids,
                )
            )
    return summaries
