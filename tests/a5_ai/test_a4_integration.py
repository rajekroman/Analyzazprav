from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from analyzazprav.a5_ai.integration_a4 import (
    A4MessageSource,
    candidate_from_a4_change_point,
    candidate_from_a4_conflict,
    candidate_from_a4_engagement,
    candidate_from_a4_regime,
    candidate_from_a4_topic,
)

UTC = timezone.utc
BASE = datetime(2025, 5, 10, 12, 0, tzinfo=UTC)
BASE_US = int(BASE.timestamp() * 1_000_000)


@dataclass
class Conflict:
    conversation_id: int = 7
    session_id: int = 3
    score: float = 0.84
    start_us: int = BASE_US
    end_us: int = BASE_US + 1_200_000_000
    factors: dict[str, float] = None
    source_message_ids: tuple[int, ...] = (1, 2)
    def __post_init__(self):
        if self.factors is None: self.factors = {"negative": 0.7, "rapid_exchange": 0.5}


@dataclass
class Change:
    conversation_id: int = 7
    participant_id: int = 11
    metric: str = "median_response_latency_seconds"
    period_date: str = "2025-05-10"
    value: float = 900.0
    baseline_median: float = 180.0
    robust_z_score: float = 4.2
    direction: str = "increasing"
    source_message_ids: tuple[int, ...] = (2, 3)


@dataclass
class Engagement:
    conversation_id: int = 7
    participant_id: int = 11
    period_start: str = "2025-05-05"
    period_end: str = "2025-05-11"
    score: float = -0.7
    direction: str = "decrease"
    component_scores: dict[str, float] = None
    source_message_ids: tuple[int, ...] = (1, 2, 3)
    def __post_init__(self):
        if self.component_scores is None: self.component_scores = {"initiation": -0.4}


@dataclass
class Regime:
    conversation_id: int = 7
    period_start: str = "2025-05-05"
    period_end: str = "2025-05-11"
    participant_a_id: int = 11
    participant_a_direction: str = "increase"
    participant_a_score: float = 0.8
    participant_b_id: int = 22
    participant_b_direction: str = "decrease"
    participant_b_score: float = -0.8
    regime_type: str = "opposing_directions"
    source_message_ids: tuple[int, ...] = (1, 2, 3, 4)


@dataclass
class Topic:
    conversation_id: int = 7
    topic_key: str = "trip"
    method: str = "lexical_ngram_v1"
    normalized_phrase: str = "praha vikend"
    ngram_size: int = 2
    document_frequency: int = 4
    document_frequency_ratio: float = 0.2
    occurrence_count: int = 6
    participant_count: int = 2
    salience: float = 1.5
    first_period_date: str = "2025-05-01"
    last_period_date: str = "2025-05-20"
    source_message_ids: tuple[int, ...] = (5, 7, 9, 12)


@dataclass
class Message:
    message_id: int
    conversation_id: int
    participant_id: int | None
    timestamp_us: int | None
    text_clean: str = ""
    has_attachment: bool = False


class A4IntegrationTests(unittest.TestCase):
    def test_conflict_preserves_current_a4_evidence(self):
        candidate = candidate_from_a4_conflict(Conflict())
        self.assertEqual(candidate.importance_score, 84.0)
        self.assertEqual(candidate.evidence_message_ids, ("1", "2"))
        self.assertEqual(candidate.metrics_during["conflict_score"], 0.84)

    def test_change_point_preserves_metric_and_baseline(self):
        candidate = candidate_from_a4_change_point(Change())
        self.assertEqual(candidate.candidate_type, "change_point")
        self.assertEqual(candidate.metrics_during["baseline_median"], 180.0)
        self.assertEqual(candidate.metadata["metric"], "median_response_latency_seconds")
        self.assertEqual(candidate.evidence_message_ids, ("2", "3"))

    def test_engagement_and_regime_remain_deterministic_labels(self):
        engagement = candidate_from_a4_engagement(Engagement())
        regime = candidate_from_a4_regime(Regime())
        self.assertEqual(engagement.detected_signals, ("engagement:decrease",))
        self.assertEqual(regime.detected_signals, ("opposing_directions",))
        self.assertEqual(regime.metadata["participant_b_direction"], "decrease")

    def test_lexical_topic_is_not_promoted_to_semantic_fact(self):
        candidate = candidate_from_a4_topic(Topic())
        self.assertEqual(candidate.candidate_type, "lexical_topic")
        self.assertEqual(candidate.importance_score, 65.0)
        self.assertEqual(candidate.metadata["method"], "lexical_ngram_v1")
        self.assertEqual(candidate.detected_signals, ("lexical_topic_candidate",))

    def test_a4_message_source_uses_canonical_ids_and_utc(self):
        source = A4MessageSource.from_a4_messages([
            Message(1, 7, 11, BASE_US, "hello"),
            Message(2, 7, 22, BASE_US + 60_000_000, "photo", True),
            Message(3, 8, 33, BASE_US, "ignore"),
            Message(4, 7, 11, None, "undated"),
        ])
        selected = source.list_messages("7", BASE, datetime.fromtimestamp((BASE_US + 120_000_000) / 1_000_000, tz=UTC))
        self.assertEqual([m.id for m in selected], ["1", "2"])
        self.assertEqual(selected[1].attachment_types, ("attachment",))


if __name__ == "__main__": unittest.main()
