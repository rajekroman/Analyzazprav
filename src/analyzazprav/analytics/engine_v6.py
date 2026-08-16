from __future__ import annotations

from typing import Iterable

from .config import AnalyticsConfig
from .engine_v5 import analyze_conversation as _analyze_conversation_v5
from .models import AnalyticMessage, ConversationAnalytics
from .topic_markers import TOPIC_MARKER_METHOD, build_topic_marker_evidence
from .topics import build_lexical_topic_candidates


def analyze_conversation(
    messages: Iterable[AnalyticMessage], config: AnalyticsConfig | None = None
) -> ConversationAnalytics:
    """Run stable v5 analytics and append lexical/topic-marker evidence."""

    cfg = config or AnalyticsConfig()
    source = list(messages)
    result = _analyze_conversation_v5(source, cfg)
    candidates, evidence = build_lexical_topic_candidates(source, cfg)
    result.topic_candidates = candidates
    result.topic_evidence = evidence
    result.topic_marker_evidence = build_topic_marker_evidence(source, evidence, cfg)
    result.diagnostics["topic_method"] = "lexical_ngram_v1"
    result.diagnostics["topic_candidate_count"] = len(candidates)
    result.diagnostics["topic_marker_method"] = TOPIC_MARKER_METHOD
    result.diagnostics["topic_marker_evidence_count"] = len(result.topic_marker_evidence)
    return result
