from __future__ import annotations

from typing import Iterable

from .config import AnalyticsConfig
from .engine_v5 import analyze_conversation as _analyze_conversation_v5
from .models import AnalyticMessage, ConversationAnalytics
from .topics import build_lexical_topic_candidates


def analyze_conversation(
    messages: Iterable[AnalyticMessage], config: AnalyticsConfig | None = None
) -> ConversationAnalytics:
    """Run stable v5 analytics and append deterministic lexical topic evidence."""

    cfg = config or AnalyticsConfig()
    source = list(messages)
    result = _analyze_conversation_v5(source, cfg)
    candidates, evidence = build_lexical_topic_candidates(source, cfg)
    result.topic_candidates = candidates
    result.topic_evidence = evidence
    result.diagnostics["topic_method"] = "lexical_ngram_v1"
    result.diagnostics["topic_candidate_count"] = len(candidates)
    return result
