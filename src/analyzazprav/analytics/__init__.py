"""Deterministic A4 analytics over the integrated A2/A3 processing contract."""

from .adapter import analyze_database, load_analytic_messages
from .config import AnalyticsConfig
from .core import build_turns
from .engine_v6 import analyze_conversation
from .incremental import analyze_incremental_database
from .models import (
    AnalyticMessage,
    ChangePoint,
    ConversationAnalytics,
    DailyParticipantMetric,
    DyadicRegime,
    EngagementPeriodSignal,
    PeriodParticipantMetric,
    SilenceEvent,
    TimeBucketMetric,
    TopicCandidate,
    TopicEvidence,
    TrendSummary,
)
from .store_v7 import AnalyticsStore
from .topics import build_lexical_topic_candidates, tokenize_topic_text

__all__ = [
    "AnalyticMessage",
    "AnalyticsConfig",
    "AnalyticsStore",
    "ChangePoint",
    "ConversationAnalytics",
    "DailyParticipantMetric",
    "DyadicRegime",
    "EngagementPeriodSignal",
    "PeriodParticipantMetric",
    "SilenceEvent",
    "TimeBucketMetric",
    "TopicCandidate",
    "TopicEvidence",
    "TrendSummary",
    "analyze_conversation",
    "analyze_database",
    "analyze_incremental_database",
    "build_lexical_topic_candidates",
    "build_turns",
    "load_analytic_messages",
    "tokenize_topic_text",
]
