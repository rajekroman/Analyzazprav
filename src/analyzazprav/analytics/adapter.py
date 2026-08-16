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


def _columns(conn: sqlite3.Connection, name: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({name})")}


def _latest_completed_processing_run_id(conn: sqlite3.Connection) -> int | None:
    try:
        row = conn.execute(
            "SELECT id FROM processing_run WHERE status = 'completed' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return None if row is None else int(row[0])


def _analytic_query(
    conn: sqlite3.Connection,
    conversation_id: int | None,
) -> tuple[str, tuple[int, ...], bool]:
    """Build the A4 read query over the integrated A2/A3 contract.

    Production databases expose `analysis_messages.membership_id` plus an A3
    latest projection. A3 v5 adds `analysis_processed_messages_resolved_latest`,
    which is preferred because it preserves A2 memberships while supplying the
    conservative resolved sender id. The older latest view remains a compatible
    fallback for A3 v4 databases.

    A narrow legacy-fixture fallback remains only so isolated unit tests can
    exercise A4 calculations without recreating the whole A1-A3 schema.
    """

    am_columns = _columns(conn, "analysis_messages")
    resolved_columns = _columns(conn, "analysis_processed_messages_resolved_latest")
    latest_columns = _columns(conn, "analysis_processed_messages_latest")
    has_membership_contract = "membership_id" in am_columns
    integrated_contract = has_membership_contract and bool(
        resolved_columns or latest_columns
    )

    params: list[int] = []
    filters: list[str] = []
    if conversation_id is not None:
        filters.append("am.conversation_id = ?")
        params.append(int(conversation_id))
    where_clause = "WHERE " + " AND ".join(filters) if filters else ""

    if integrated_contract:
        if resolved_columns:
            processed_source = "analysis_processed_messages_resolved_latest"
            participant_expr = "COALESCE(pm.resolved_sender_id, am.sender_id)"
        else:
            processed_source = "analysis_processed_messages_latest"
            participant_expr = "am.sender_id"

        query = f"""
SELECT am.id,
       am.conversation_id,
       {participant_expr} AS participant_id,
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
       pm.local_hour,
       am.membership_id
FROM analysis_messages AS am
JOIN {processed_source} AS pm
  ON pm.membership_id = am.membership_id
 AND pm.message_id = am.id
 AND pm.conversation_id = am.conversation_id
{where_clause}
ORDER BY am.conversation_id, pm.sequence_number, am.membership_id
"""
        return query, tuple(params), True

    pm_columns = _columns(conn, "processed_message")
    if not pm_columns:
        raise RuntimeError("A4 requires A3 processed_message data")

    has_pm_run = "processing_run_id" in pm_columns
    has_pm_conversation = "conversation_id" in pm_columns
    has_pm_membership = "membership_id" in pm_columns
    has_am_membership = "membership_id" in am_columns
    has_resolved_sender = "resolved_sender_id" in pm_columns
    joins = ["pm.message_id = am.id"]
    fallback_params: list[int] = []

    if has_pm_conversation:
        joins.append("pm.conversation_id = am.conversation_id")
    if has_pm_membership and has_am_membership:
        joins.append("pm.membership_id = am.membership_id")
    if has_pm_run:
        processing_run_id = _latest_completed_processing_run_id(conn)
        if processing_run_id is None:
            raise RuntimeError("A4 requires a completed A3 processing_run")
        joins.append("pm.processing_run_id = ?")
        fallback_params.append(processing_run_id)

    fallback_filters: list[str] = []
    if conversation_id is not None:
        fallback_filters.append("am.conversation_id = ?")
        fallback_params.append(int(conversation_id))
    fallback_where = (
        "WHERE " + " AND ".join(fallback_filters) if fallback_filters else ""
    )
    participant_expr = (
        "COALESCE(pm.resolved_sender_id, am.sender_id)"
        if has_resolved_sender
        else "am.sender_id"
    )
    membership_expr = "am.membership_id" if has_am_membership else "NULL"
    membership_order = ", am.membership_id" if has_am_membership else ""

    query = f"""
SELECT am.id,
       am.conversation_id,
       {participant_expr} AS participant_id,
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
       pm.local_hour,
       {membership_expr}
FROM analysis_messages AS am
JOIN processed_message AS pm
  ON {' AND '.join(joins)}
{fallback_where}
ORDER BY am.conversation_id, pm.sequence_number, am.id{membership_order}
"""
    return query, tuple(fallback_params), False


def load_analytic_messages(
    conn: sqlite3.Connection, conversation_id: int | None = None
) -> list[AnalyticMessage]:
    query, params, integrated_contract = _analytic_query(conn, conversation_id)
    rows = conn.execute(query, params)
    messages = [
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
            membership_id=None if row[22] is None else int(row[22]),
        )
        for row in rows
    ]

    seen_memberships: set[int] = set()
    duplicate_memberships: list[int] = []
    seen_conversation_messages: set[tuple[int, int]] = set()
    duplicate_conversation_messages: list[tuple[int, int]] = []
    for message in messages:
        if message.membership_id is not None:
            if message.membership_id in seen_memberships:
                duplicate_memberships.append(message.membership_id)
            seen_memberships.add(message.membership_id)
        key = (message.conversation_id, message.message_id)
        if key in seen_conversation_messages:
            duplicate_conversation_messages.append(key)
        seen_conversation_messages.add(key)

    if integrated_contract and any(message.membership_id is None for message in messages):
        raise RuntimeError("A4 integrated contract requires membership_id for every message")
    if duplicate_memberships or duplicate_conversation_messages:
        raise RuntimeError(
            "A4 A3-contract reconciliation failed; duplicate memberships="
            f"{sorted(set(duplicate_memberships))}, duplicate conversation/message="
            f"{sorted(set(duplicate_conversation_messages))}"
        )
    return messages


def conversation_fingerprint(messages: Iterable[AnalyticMessage]) -> str:
    """Hash every A2/A3 field that can materially affect A4 output."""

    digest = hashlib.sha256()
    for message in sorted(
        messages,
        key=lambda item: (
            item.sequence_number,
            item.membership_id if item.membership_id is not None else -1,
            item.message_id,
        ),
    ):
        payload = (
            message.membership_id,
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
    """Return latest source fingerprint + analysis signature by conversation."""

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
    """Recompute whole conversations when source data or analysis rules changed."""

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
