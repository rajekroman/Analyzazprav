from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import sqlite3
from typing import Iterable

from .config import AnalyticsConfig
from .engine_v6 import analyze_conversation
from .models import AnalyticMessage, ConversationAnalytics
from .versioning import analysis_signature


def _date_string(year: int | None, month: int | None, day: int | None) -> str | None:
    if year is None or month is None or day is None:
        return None
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


_QUERY = """
SELECT am.id,
       am.conversation_id,
       am.sender_id,
       am.sent_at_utc_us,
       COALESCE(pm.text_clean, ''),
       pm.session_id,
       pm.sequence_number,
       pm.word_count,
       pm.char_count,
       pm.question_mark_count,
       pm.exclamation_mark_count,
       pm.has_attachment,
       pm.utc_year,
       pm.utc_month,
       pm.utc_day,
       pm.utc_weekday,
       pm.utc_hour,
       pm.local_year,
       pm.local_month,
       pm.local_day,
       pm.local_weekday,
       pm.local_hour
FROM analysis_messages AS am
JOIN processed_message AS pm ON pm.message_id = am.id
{where_clause}
ORDER BY am.conversation_id, pm.sequence_number, am.id
"""


def load_analytic_messages(
    conn: sqlite3.Connection, conversation_id: int | None = None
) -> list[AnalyticMessage]:
    where = "WHERE am.conversation_id = ?" if conversation_id is not None else ""
    params: tuple[int, ...] = (conversation_id,) if conversation_id is not None else ()
    rows = conn.execute(_QUERY.format(where_clause=where), params)
    return [
        AnalyticMessage(
            message_id=int(row[0]),
            conversation_id=int(row[1]),
            participant_id=None if row[2] is None else int(row[2]),
            timestamp_us=None if row[3] is None else int(row[3]),
            text_clean=str(row[4] or ""),
            session_id=int(row[5]),
            sequence_number=int(row[6]),
            word_count=int(row[7]),
            character_count=int(row[8]),
            question_mark_count=int(row[9]),
            exclamation_mark_count=int(row[10]),
            has_attachment=bool(row[11]),
            utc_date=_date_string(row[12], row[13], row[14]),
            utc_weekday=None if row[15] is None else int(row[15]),
            utc_hour=None if row[16] is None else int(row[16]),
            local_date=_date_string(row[17], row[18], row[19]),
            local_weekday=None if row[20] is None else int(row[20]),
            local_hour=None if row[21] is None else int(row[21]),
        )
        for row in rows
    ]


def conversation_fingerprint(messages: Iterable[AnalyticMessage]) -> str:
    """Hash every A2/A3 field that can materially affect A4 output."""

    digest = hashlib.sha256()
    for message in sorted(messages, key=lambda item: (item.sequence_number, item.message_id)):
        payload = (
            message.message_id,
            message.conversation_id,
            message.participant_id,
            message.timestamp_us,
            message.text_clean,
            message.session_id,
            message.sequence_number,
            message.word_count,
            message.character_count,
            message.question_mark_count,
            message.exclamation_mark_count,
            message.has_attachment,
            message.utc_date,
            message.utc_weekday,
            message.utc_hour,
            message.local_date,
            message.local_weekday,
            message.local_hour,
        )
        digest.update(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def _group_messages(
    messages: Iterable[AnalyticMessage],
    conversation_ids: Iterable[int] | None = None,
) -> dict[int, list[AnalyticMessage]]:
    selected = set(conversation_ids) if conversation_ids is not None else None
    grouped: dict[int, list[AnalyticMessage]] = defaultdict(list)
    for message in messages:
        if selected is not None and message.conversation_id not in selected:
            continue
        grouped[message.conversation_id].append(message)
    return grouped


def _analyze_grouped(
    grouped: dict[int, list[AnalyticMessage]],
    config: AnalyticsConfig,
) -> list[ConversationAnalytics]:
    results: list[ConversationAnalytics] = []
    for conversation_id in sorted(grouped):
        source = grouped[conversation_id]
        result = analyze_conversation(source, config)
        result.source_fingerprint = conversation_fingerprint(source)
        results.append(result)
    return results


def analyze_database(
    conn: sqlite3.Connection,
    config: AnalyticsConfig | None = None,
    conversation_ids: Iterable[int] | None = None,
) -> list[ConversationAnalytics]:
    cfg = config or AnalyticsConfig()
    grouped = _group_messages(load_analytic_messages(conn), conversation_ids)
    return _analyze_grouped(grouped, cfg)


def _latest_analysis_states(conn: sqlite3.Connection) -> dict[int, tuple[str, str]]:
    """Return latest v6 source fingerprint + analysis signature by conversation."""

    try:
        rows = conn.execute(
            """SELECT s.conversation_id, s.source_fingerprint, s.analysis_signature
               FROM analytics_conversation_state_v6 AS s
               JOIN analysis_a4_latest_conversation_run AS r
                 ON r.conversation_id = s.conversation_id
                AND r.analytics_run_id = s.analytics_run_id"""
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {
        int(row[0]): (str(row[1]), str(row[2]))
        for row in rows
        if row[1] is not None and row[2] is not None
    }


def analyze_incremental_database(
    conn: sqlite3.Connection,
    config: AnalyticsConfig | None = None,
    conversation_ids: Iterable[int] | None = None,
) -> list[ConversationAnalytics]:
    """Recompute whole conversations when source *or analysis rules* changed.

    A new import can shift A3 session and turn context retroactively. Whole-
    conversation granularity is therefore conservative and reproducible. v6
    also tracks an analysis signature so a code/config version change cannot
    silently reuse stale derived metrics.
    """

    cfg = config or AnalyticsConfig()
    grouped = _group_messages(load_analytic_messages(conn), conversation_ids)
    previous = _latest_analysis_states(conn)
    expected_signature = analysis_signature(cfg)

    changed: dict[int, list[AnalyticMessage]] = {}
    for conversation_id, source in grouped.items():
        current_source_fingerprint = conversation_fingerprint(source)
        if previous.get(conversation_id) != (
            current_source_fingerprint,
            expected_signature,
        ):
            changed[conversation_id] = source

    return _analyze_grouped(changed, cfg)
