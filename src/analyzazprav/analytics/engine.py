from __future__ import annotations

from collections import Counter
from typing import Iterable

from .config import AnalyticsConfig
from .core import (
    build_sessions,
    build_turns,
    conflict_candidates,
    participant_metrics,
    reciprocity_metrics,
    response_latencies,
)
from .models import ConversationAnalytics, Message


def analyze_conversation(
    messages: Iterable[Message], config: AnalyticsConfig | None = None
) -> ConversationAnalytics:
    """Run deterministic A4 analytics for one normalized conversation.

    The function is deliberately side-effect free. Persistence belongs to the
    database integration layer, which can store this reproducible result and
    trace every event back to source message IDs.
    """

    cfg = config or AnalyticsConfig()
    source = list(messages)
    if not source:
        return ConversationAnalytics(
            conversation_id="",
            message_count=0,
            turn_count=0,
            session_count=0,
            diagnostics={"excluded_reactions": 0, "source_message_count": 0},
        )

    conversation_ids = {message.conversation_id for message in source}
    if len(conversation_ids) != 1:
        raise ValueError("analyze_conversation expects exactly one conversation")

    analytic_messages = [message for message in source if not message.is_reaction]
    excluded_reactions = len(source) - len(analytic_messages)
    conversation_id = next(iter(conversation_ids))

    turns = build_turns(analytic_messages, cfg)
    sessions = build_sessions(turns, cfg)
    latencies = response_latencies(turns, sessions)
    metrics = participant_metrics(analytic_messages, turns, sessions, latencies, cfg)
    reciprocity = reciprocity_metrics(metrics)
    conflicts = conflict_candidates(turns, sessions, cfg)

    accounted = Counter(message.message_id for message in analytic_messages)
    duplicates = sorted(message_id for message_id, count in accounted.items() if count > 1)

    return ConversationAnalytics(
        conversation_id=conversation_id,
        message_count=len(analytic_messages),
        turn_count=len(turns),
        session_count=len(sessions),
        participant_metrics=metrics,
        latency_samples=latencies,
        conflicts=conflicts,
        sessions=sessions,
        turns=turns,
        diagnostics={
            "source_message_count": len(source),
            "excluded_reactions": excluded_reactions,
            "duplicate_message_ids": duplicates,
            "reciprocity": reciprocity,
            "accounting_ok": len(analytic_messages) + excluded_reactions == len(source),
        },
    )
