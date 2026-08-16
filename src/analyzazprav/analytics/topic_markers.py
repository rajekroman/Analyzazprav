from __future__ import annotations

from typing import Iterable, Sequence

from .config import AnalyticsConfig
from .core import _marker_hits
from .models import AnalyticMessage, TopicEvidence, TopicMarkerEvidence

TOPIC_MARKER_METHOD = "topic_marker_cooccurrence_v1"


def build_topic_marker_evidence(
    messages: Iterable[AnalyticMessage],
    topic_evidence: Sequence[TopicEvidence],
    config: AnalyticsConfig | None = None,
) -> list[TopicMarkerEvidence]:
    """Return sparse same-message topic/marker co-occurrence evidence.

    Topic discovery remains authoritative in `topics.py`. This layer only checks
    the exact messages already selected as topic evidence using the same marker
    counting helper used by the core A4 metrics. Rows with no configured marker
    hit are intentionally omitted; neutral topic evidence remains available in
    `analytics_topic_evidence`.
    """

    cfg = config or AnalyticsConfig()
    source = list(messages)
    if not source or not topic_evidence:
        return []

    conversation_ids = {message.conversation_id for message in source}
    if len(conversation_ids) != 1:
        raise ValueError("build_topic_marker_evidence expects exactly one conversation")
    conversation_id = source[0].conversation_id

    by_message_id: dict[int, AnalyticMessage] = {}
    for message in source:
        if message.message_id in by_message_id:
            raise ValueError(
                "build_topic_marker_evidence requires unique message ids inside one conversation"
            )
        by_message_id[message.message_id] = message

    rows: list[TopicMarkerEvidence] = []
    for evidence in topic_evidence:
        if evidence.conversation_id != conversation_id:
            raise ValueError("topic evidence conversation does not match source messages")
        message = by_message_id.get(evidence.message_id)
        if message is None:
            raise ValueError(
                f"topic evidence references missing source message {evidence.message_id}"
            )

        affection_hits = _marker_hits(message.text_clean, cfg.affection_markers)
        negative_hits = _marker_hits(message.text_clean, cfg.negative_markers)
        if affection_hits == 0 and negative_hits == 0:
            continue

        rows.append(
            TopicMarkerEvidence(
                conversation_id=conversation_id,
                topic_key=evidence.topic_key,
                message_id=evidence.message_id,
                affection_hit_count=affection_hits,
                negative_hit_count=negative_hits,
            )
        )

    rows.sort(key=lambda row: (row.topic_key, row.message_id))
    return rows
