from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AnalyticsConfig:
    """Transparent thresholds/weights for deterministic A4 calculations."""

    rapid_exchange_seconds: int = 5 * 60
    responsiveness_reference_seconds: int = 6 * 60 * 60
    post_silence_reference_seconds: int = 18 * 60 * 60
    conflict_threshold: float = 0.55
    change_baseline_window_days: int = 28
    change_min_baseline_days: int = 7
    change_z_threshold: float = 2.5

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

    def __post_init__(self) -> None:
        if self.rapid_exchange_seconds <= 0:
            raise ValueError("rapid_exchange_seconds must be positive")
        if self.responsiveness_reference_seconds <= 0:
            raise ValueError("responsiveness_reference_seconds must be positive")
        if self.post_silence_reference_seconds <= 0:
            raise ValueError("post_silence_reference_seconds must be positive")
        if not 0 <= self.conflict_threshold <= 1:
            raise ValueError("conflict_threshold must be between 0 and 1")
        if self.change_baseline_window_days < self.change_min_baseline_days:
            raise ValueError("change baseline window must be >= minimum baseline days")
        if self.change_min_baseline_days < 2:
            raise ValueError("change_min_baseline_days must be >= 2")
        if self.change_z_threshold <= 0:
            raise ValueError("change_z_threshold must be positive")
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

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
