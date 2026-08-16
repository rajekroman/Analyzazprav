from __future__ import annotations

from typing import Iterable

from .config import AnalyticsConfig
from .core import analyze_conversation as _analyze_conversation_v4
from .models import AnalyticMessage, ConversationAnalytics
from .slopes import build_trend_summaries


def analyze_conversation(
    messages: Iterable[AnalyticMessage], config: AnalyticsConfig | None = None
) -> ConversationAnalytics:
    """Run the stable v4 core and append v5 interval trend summaries."""

    cfg = config or AnalyticsConfig()
    result = _analyze_conversation_v4(messages, cfg)
    result.trend_summaries = build_trend_summaries(result.period_metrics, cfg)
    return result
