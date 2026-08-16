from __future__ import annotations

from collections import defaultdict
import sqlite3
from typing import Iterable

from .adapter import conversation_fingerprint, load_analytic_messages
from .config import AnalyticsConfig
from .engine_v6 import analyze_conversation
from .models import AnalyticMessage, ConversationAnalytics
from .versioning import analysis_signature


def _latest_completed_processing_run_id(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT id FROM processing_run WHERE status='completed' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise RuntimeError("A4 requires a completed A3 processing_run")
    return int(row[0])


def _latest_states(conn: sqlite3.Connection) -> dict[int, tuple[str, str, int]]:
    """Latest source fingerprint, analysis signature and exact A3 run by conversation."""

    try:
        rows = conn.execute(
            """SELECT s.conversation_id,
                      s.source_fingerprint,
                      s.analysis_signature,
                      ar.processing_run_id
               FROM analytics_conversation_state_v6 AS s
               JOIN analysis_a4_latest_conversation_run AS latest
                 ON latest.conversation_id=s.conversation_id
                AND latest.analytics_run_id=s.analytics_run_id
               JOIN analytics_run AS ar ON ar.id=s.analytics_run_id"""
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {
        int(row[0]): (str(row[1]), str(row[2]), int(row[3]))
        for row in rows
        if row[1] is not None and row[2] is not None and row[3] is not None
    }


def analyze_incremental_database(
    conn: sqlite3.Connection,
    config: AnalyticsConfig | None = None,
    conversation_ids: Iterable[int] | None = None,
) -> list[ConversationAnalytics]:
    """Recompute whole conversations when data, rules, or A3 provenance changed.

    A4 evidence references A3 sessions whose identity is scoped by processing_run_id.
    Even a byte-for-byte identical deterministic A3 rerun creates a new provenance
    namespace. Therefore an A3 run change invalidates the A4 conversation state.
    This deliberately prefers traceability over avoiding a reproducible recompute.
    """

    cfg = config or AnalyticsConfig()
    selected = None if conversation_ids is None else {int(value) for value in conversation_ids}
    grouped: dict[int, list[AnalyticMessage]] = defaultdict(list)
    for message in load_analytic_messages(conn):
        if selected is None or message.conversation_id in selected:
            grouped[message.conversation_id].append(message)

    previous = _latest_states(conn)
    expected_signature = analysis_signature(cfg)
    current_processing_run_id = _latest_completed_processing_run_id(conn)

    results: list[ConversationAnalytics] = []
    for conversation_id in sorted(grouped):
        source = grouped[conversation_id]
        fingerprint = conversation_fingerprint(source)
        if previous.get(conversation_id) == (
            fingerprint,
            expected_signature,
            current_processing_run_id,
        ):
            continue
        result = analyze_conversation(source, cfg)
        result.source_fingerprint = fingerprint
        results.append(result)
    return results
