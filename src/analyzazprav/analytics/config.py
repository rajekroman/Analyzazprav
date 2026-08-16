from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class AnalyticsConfig:
    """Configuration for deterministic A4 analytics.

    All thresholds are explicit so the UI can expose them and QA can reproduce
    every result from the same normalized source data.
    """

    session_gap_seconds: int = 6 * 60 * 60
    turn_gap_seconds: int = 30 * 60
    rapid_exchange_seconds: int = 5 * 60
    conflict_threshold: float = 0.55

    affection_markers: tuple[str, ...] = field(
        default=(
            "❤️",
            "❤",
            "💕",
            "😘",
            "🥰",
            "miluju",
            "miláčku",
            "zlatíčko",
            "love you",
        )
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

    def __post_init__(self) -> None:
        if self.session_gap_seconds <= 0:
            raise ValueError("session_gap_seconds must be > 0")
        if self.turn_gap_seconds <= 0:
            raise ValueError("turn_gap_seconds must be > 0")
        if self.turn_gap_seconds > self.session_gap_seconds:
            raise ValueError("turn_gap_seconds cannot exceed session_gap_seconds")
        if not 0 <= self.conflict_threshold <= 1:
            raise ValueError("conflict_threshold must be between 0 and 1")

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
