from __future__ import annotations

from collections import defaultdict
import sqlite3
from typing import Iterable

from .config import AnalyticsConfig
from .core import analyze_conversation
from .models import AnalyticMessage, ConversationAnalytics


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
       pm.has_attachment
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
        )
        for row in rows
    ]


def analyze_database(
    conn: sqlite3.Connection,
    config: AnalyticsConfig | None = None,
    conversation_ids: Iterable[int] | None = None,
) -> list[ConversationAnalytics]:
    selected = set(conversation_ids) if conversation_ids is not None else None
    grouped: dict[int, list[AnalyticMessage]] = defaultdict(list)
    for message in load_analytic_messages(conn):
        if selected is not None and message.conversation_id not in selected:
            continue
        grouped[message.conversation_id].append(message)
    return [
        analyze_conversation(grouped[conversation_id], config)
        for conversation_id in sorted(grouped)
    ]
