"""Deterministic A4 analytics over the A2/A3 canonical processing contract."""

from .adapter import analyze_database, load_analytic_messages
from .config import AnalyticsConfig
from .core import analyze_conversation, build_turns
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
)
from .store import AnalyticsStore

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
    "analyze_conversation",
    "analyze_database",
    "build_turns",
    "load_analytic_messages",
]
