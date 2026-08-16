from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import sqlite3
from typing import Iterable

from .config import AnalyticsConfig
from .engine_v6 import analyze_conversation
from .models import AnalyticMessage, ConversationAnalytics


def _date_string(year: int | None, month: int | None, day: int | None) -> str | None:
    if year is None or month is None or day is None:
        return None
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def _columns(conn: sqlite3.Connection, name: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({name})")}


def _latest_completed_processing_run_id(conn: sqlite3.Connection) -> int | None:
    try:
        row = conn.execute(
            "SELECT id FROM processing_run WHERE status='completed' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return None if row is None else int(row[0])


def _analytic_query(
    conn: sqlite3.Connection,
    conversation_id: int | None,
) -> tuple[str, tuple[int, ...], bool]:
    """Build the A4 read query over the current A2/A3 contract.

    Membership identity is authoritative for source accounting. Participant
    attribution uses A3's resolved sender when available, falling back to the
    canonical A2 sender. Resolution never changes message/membership provenance.
    """

    am_columns = _columns(conn, "analysis_messages")
    resolved_latest_columns = _columns(conn, "analysis_processed_messages_resolved_latest")
    basic_latest_columns = _columns(conn, "analysis_processed_messages_latest")

    use_resolved_latest = (
        "membership_id" in am_columns
        and "membership_id" in resolved_latest_columns
        and "resolved_sender_id" in resolved_latest_columns
    )
    use_basic_latest = (
        not use_resolved_latest
        and "membership_id" in am_columns
        and "membership_id" in basic_latest_columns
    )
    integrated_contract = use_resolved_latest or use_basic_latest

    params: list[int] = []
    filters: list[str] = []
    if conversation_id is not None:
        filters.append("am.conversation_id = ?")
        params.append(int(conversation_id))
    where_clause = "WHERE " + " AND ".join(filters) if filters else ""

    if integrated_contract:
        source_view = (
            "analysis_processed_messages_resolved_latest"
            if use_resolved_latest
            else "analysis_processed_messages_latest"
        )
        participant_expr = (
            "COALESCE(pm.resolved_sender_id, am.sender_id)"
            if use_resolved_latest
            else "am.sender_id"
        )
        query = f"""
SELECT am.id,
       am.conversation_id,
       {participant_expr} AS analytic_participant_id,
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
JOIN {source_view} AS pm
  ON pm.membership_id = am.membership_id
 AND pm.message_id = am.id
 AND pm.conversation_id = am.conversation_id
{where_clause}
ORDER BY am.conversation_id, pm.sequence_number, am.membership_id
"""
        return query, tuple(params), True

    # Narrow legacy-fixture fallback for isolated A4 calculation tests.
    pm_columns = _columns(conn, "processed_message")
    if not pm_columns:
        raise RuntimeError("A4 requires A3 processed_message data")

    has_pm_run = "processing_run_id" in pm_columns
    has_pm_conversation = "conversation_id" in pm_columns
    has_pm_membership = "membership_id" in pm_columns
    has_am_membership = "membership_id" in am_columns
    resolved_sender_columns = _columns(conn, "processed_message_resolved_sender")
    can_join_resolved = (
        has_pm_run
        and has_pm_membership
        and "processing_run_id" in resolved_sender_columns
        and "membership_id" in resolved_sender_columns
        and "resolved_participant_id" in resolved_sender_columns
    )

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
    membership_expr = "am.membership_id" if has_am_membership else "NULL"
    membership_order = ", am.membership_id" if has_am_membership else ""
    resolved_join = ""
    participant_expr = "am.sender_id"
    if can_join_resolved:
        resolved_join = """
LEFT JOIN processed_message_resolved_sender AS pmrs
  ON pmrs.processing_run_id = pm.processing_run_id
 AND pmrs.membership_id = pm.membership_id
"""
        participant_expr = "COALESCE(pmrs.resolved_participant_id, am.sender_id)"

    query = f"""
SELECT am.id,
       am.conversation_id,
       {participant_expr} AS analytic_participant_id,
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
{resolved_join}
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
    seen_conversation_messages: set[tuple[int, int]] = set()
    duplicate_evidence: list[str] = []
    for message in messages:
        conversation_message = (message.conversation_id, message.message_id)
        if conversation_message in seen_conversation_messages:
            duplicate_evidence.append(f"conversation/message={conversation_message}")
        seen_conversation_messages.add(conversation_message)

        if message.membership_id is not None:
            if message.membership_id in seen_memberships:
                duplicate_evidence.append(f"membership={message.membership_id}")
            seen_memberships.add(message.membership_id)
        elif integrated_contract:
            duplicate_evidence.append(
                f"missing-membership=({message.conversation_id},{message.message_id})"
            )

    if duplicate_evidence:
        raise RuntimeError(
            "A4 A2/A3 reconciliation failed: " + ", ".join(sorted(set(duplicate_evidence)))
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
