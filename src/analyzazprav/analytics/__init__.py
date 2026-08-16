"""Deterministic A4 analytics over the A2/A3 canonical processing contract."""

from .adapter import analyze_database, analyze_incremental_database, load_analytic_messages
from .config import AnalyticsConfig
from .core import build_turns
from .engine_v5 import analyze_conversation
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
    TrendSummary,
)
from .store_v5 import AnalyticsStore

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
    "TrendSummary",
    "analyze_conversation",
    "analyze_database",
    "analyze_incremental_database",
    "build_turns",
    "load_analytic_messages",
]
