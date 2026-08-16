from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AnalyticsConfig:
    """Transparent thresholds/weights for deterministic A4 calculations."""

    rapid_exchange_seconds: int = 5 * 60
    responsiveness_reference_seconds: int = 6 * 60 * 60
    post_silence_reference_seconds: int = 18 * 60 * 60
    long_silence_seconds: int = 24 * 60 * 60
    night_start_hour: int = 0
    night_end_hour: int = 6
    conflict_threshold: float = 0.55
    change_baseline_window_days: int = 28
    change_min_baseline_days: int = 7
    change_z_threshold: float = 2.5
    regime_min_baseline_periods: int = 4
    regime_signal_threshold: float = 10.0
    regime_z_clip: float = 2.5
    weekly_trend_window_periods: int = 8
    monthly_trend_window_periods: int = 6
    trend_min_periods: int = 4
    trend_normalized_slope_threshold: float = 0.05

    topic_min_document_frequency: int = 3
    topic_max_document_frequency_ratio: float = 1.0
    topic_min_token_length: int = 2
    topic_min_ngram_size: int = 1
    topic_max_ngram_size: int = 3
    topic_max_candidates: int = 50

    affection_markers: tuple[str, ...] = field(
        default=("❤️", "❤", "💕", "😘", "🥰", "miluju", "miláčku", "zlatíčko", "love you")
    )
    negative_markers: tuple[str, ...] = field(
        default=(
            "nesnáším",
            "naštvan",
            "štve mě",
            "vadí mi",
            "zklaman",
            "lhát",
            "lžeš",
            "nech mě",
            "fuck",
            "hate",
            "angry",
        )
    )

    engagement_activity_weight: float = 0.25
    engagement_initiation_weight: float = 0.20
    engagement_responsiveness_weight: float = 0.25
    engagement_question_weight: float = 0.15
    engagement_affection_weight: float = 0.15

    conflict_negative_weight: float = 0.45
    conflict_rapid_weight: float = 0.25
    conflict_exclamation_weight: float = 0.15
    conflict_post_silence_weight: float = 0.15

    regime_activity_weight: float = 0.25
    regime_initiation_weight: float = 0.20
    regime_responsiveness_weight: float = 0.20
    regime_effort_weight: float = 0.15
    regime_question_weight: float = 0.10
    regime_affection_weight: float = 0.10

    def __post_init__(self) -> None:
        if self.rapid_exchange_seconds <= 0:
            raise ValueError("rapid_exchange_seconds must be positive")
        if self.responsiveness_reference_seconds <= 0:
            raise ValueError("responsiveness_reference_seconds must be positive")
        if self.post_silence_reference_seconds <= 0:
            raise ValueError("post_silence_reference_seconds must be positive")
        if self.long_silence_seconds <= 0:
            raise ValueError("long_silence_seconds must be positive")
        if not 0 <= self.night_start_hour <= 23:
            raise ValueError("night_start_hour must be between 0 and 23")
        if not 0 <= self.night_end_hour <= 23:
            raise ValueError("night_end_hour must be between 0 and 23")
        if self.night_start_hour == self.night_end_hour:
            raise ValueError("night interval cannot cover zero or twenty-four hours")
        if not 0 <= self.conflict_threshold <= 1:
            raise ValueError("conflict_threshold must be between 0 and 1")
        if self.change_baseline_window_days < self.change_min_baseline_days:
            raise ValueError("change baseline window must be >= minimum baseline days")
        if self.change_min_baseline_days < 2:
            raise ValueError("change_min_baseline_days must be >= 2")
        if self.change_z_threshold <= 0:
            raise ValueError("change_z_threshold must be positive")
        if self.regime_min_baseline_periods < 2:
            raise ValueError("regime_min_baseline_periods must be >= 2")
        if not 0 < self.regime_signal_threshold <= 100:
            raise ValueError("regime_signal_threshold must be in (0, 100]")
        if self.regime_z_clip <= 0:
            raise ValueError("regime_z_clip must be positive")
        if self.trend_min_periods < 2:
            raise ValueError("trend_min_periods must be >= 2")
        if self.weekly_trend_window_periods < self.trend_min_periods:
            raise ValueError("weekly trend window must be >= trend_min_periods")
        if self.monthly_trend_window_periods < self.trend_min_periods:
            raise ValueError("monthly trend window must be >= trend_min_periods")
        if self.trend_normalized_slope_threshold <= 0:
            raise ValueError("trend_normalized_slope_threshold must be positive")
        if self.topic_min_document_frequency < 2:
            raise ValueError("topic_min_document_frequency must be >= 2")
        if not 0 < self.topic_max_document_frequency_ratio <= 1:
            raise ValueError("topic_max_document_frequency_ratio must be in (0, 1]")
        if self.topic_min_token_length < 1:
            raise ValueError("topic_min_token_length must be >= 1")
        if self.topic_min_ngram_size < 1:
            raise ValueError("topic_min_ngram_size must be >= 1")
        if self.topic_max_ngram_size < self.topic_min_ngram_size:
            raise ValueError("topic_max_ngram_size must be >= topic_min_ngram_size")
        if self.topic_max_ngram_size > 5:
            raise ValueError("topic_max_ngram_size must be <= 5")
        if self.topic_max_candidates < 1:
            raise ValueError("topic_max_candidates must be >= 1")
        engagement_total = (
            self.engagement_activity_weight
            + self.engagement_initiation_weight
            + self.engagement_responsiveness_weight
            + self.engagement_question_weight
            + self.engagement_affection_weight
        )
        conflict_total = (
            self.conflict_negative_weight
            + self.conflict_rapid_weight
            + self.conflict_exclamation_weight
            + self.conflict_post_silence_weight
        )
        if abs(engagement_total - 1.0) > 1e-9:
            raise ValueError("engagement weights must sum to 1.0")
        if abs(conflict_total - 1.0) > 1e-9:
            raise ValueError("conflict weights must sum to 1.0")
        regime_total = (
            self.regime_activity_weight
            + self.regime_initiation_weight
            + self.regime_responsiveness_weight
            + self.regime_effort_weight
            + self.regime_question_weight
            + self.regime_affection_weight
        )
        if abs(regime_total - 1.0) > 1e-9:
            raise ValueError("regime weights must sum to 1.0")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
