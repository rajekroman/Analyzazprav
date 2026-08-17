from __future__ import annotations

from dataclasses import replace
import sqlite3
from typing import Iterable

from . import adapter as _membership_adapter
from .config import AnalyticsConfig
from .models import AnalyticMessage, ConversationAnalytics
from .versioning import analysis_signature


def _object_exists(conn: sqlite3.Connection, name: str, object_type: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type=? AND name=?",
            (object_type, name),
        ).fetchone()
        is not None
    )


def _latest_completed_processing_run_id(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT id FROM processing_run WHERE status='completed' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise RuntimeError("A4 requires a completed A3 processing_run")
    return int(row[0])


def _latest_analysis_states_with_processing_run(
    conn: sqlite3.Connection,
) -> dict[int, tuple[str, str, int]]:
    """Return latest A4 source/signature state with its exact A3 provenance run."""

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


def _resolve_participants(
    conn: sqlite3.Connection, messages: list[AnalyticMessage]
) -> list[AnalyticMessage]:
    """Replace raw A2 sender IDs with audited A3 v5 resolved-person IDs.

    Legacy/minimal calculation fixtures that do not contain A3 v5 participant
    sidecars keep their raw sender IDs. A real A3 v5 database is fail-closed:
    once the v5 tables exist, the resolved latest-view and exact membership
    coverage are required.

    The resolved-sidecar lookup is scoped to conversations already loaded by the
    membership adapter. This is performance-only: fail-closed reconciliation is
    unchanged for every selected conversation, while an explicit single-chat
    analysis no longer materializes resolved rows for the whole archive.
    """

    if not messages:
        return []

    has_v5_sidecars = _object_exists(conn, "resolved_participant", "table")
    if not has_v5_sidecars:
        return messages

    if not _object_exists(conn, "analysis_processed_messages_resolved_latest", "view"):
        raise RuntimeError(
            "A4 requires A3 v5 analysis_processed_messages_resolved_latest when "
            "participant-resolution sidecars are present"
        )

    conversation_ids = sorted({message.conversation_id for message in messages})
    placeholders = ",".join("?" for _ in conversation_ids)
    rows = list(
        conn.execute(
            f"""SELECT membership_id, message_id, conversation_id, resolved_sender_id
                FROM analysis_processed_messages_resolved_latest
                WHERE conversation_id IN ({placeholders})""",
            tuple(conversation_ids),
        )
    )
    by_membership: dict[int, tuple[int, int, int | None]] = {}
    duplicates: list[int] = []
    for membership_id, message_id, conversation_id, resolved_sender_id in rows:
        key = int(membership_id)
        value = (
            int(message_id),
            int(conversation_id),
            None if resolved_sender_id is None else int(resolved_sender_id),
        )
        if key in by_membership:
            duplicates.append(key)
        by_membership[key] = value
    if duplicates:
        raise RuntimeError(
            "A4 A3-v5 resolved-sender reconciliation failed: duplicate memberships "
            + repr(sorted(set(duplicates)))
        )

    resolved: list[AnalyticMessage] = []
    missing: list[int] = []
    mismatched: list[int] = []
    sender_mismatches: list[int] = []
    for message in messages:
        if message.membership_id is None:
            raise RuntimeError(
                "A4 A3-v5 resolved-sender reconciliation failed: membership_id is required"
            )
        evidence = by_membership.get(message.membership_id)
        if evidence is None:
            missing.append(message.membership_id)
            continue
        evidence_message_id, evidence_conversation_id, resolved_sender_id = evidence
        if (
            evidence_message_id != message.message_id
            or evidence_conversation_id != message.conversation_id
        ):
            mismatched.append(message.membership_id)
            continue
        if message.participant_id is not None and resolved_sender_id is None:
            sender_mismatches.append(message.membership_id)
            continue
        if message.participant_id is None and resolved_sender_id is not None:
            sender_mismatches.append(message.membership_id)
            continue
        resolved.append(replace(message, participant_id=resolved_sender_id))

    if missing or mismatched or sender_mismatches:
        details: list[str] = []
        if missing:
            details.append(f"missing={sorted(set(missing))}")
        if mismatched:
            details.append(f"identity_mismatch={sorted(set(mismatched))}")
        if sender_mismatches:
            details.append(f"sender_mismatch={sorted(set(sender_mismatches))}")
        raise RuntimeError(
            "A4 A3-v5 resolved-sender reconciliation failed: " + "; ".join(details)
        )
    return resolved


def load_analytic_messages(
    conn: sqlite3.Connection, conversation_id: int | None = None
) -> list[AnalyticMessage]:
    base = _membership_adapter.load_analytic_messages(conn, conversation_id)
    return _resolve_participants(conn, base)


def _load_selected_messages(
    conn: sqlite3.Connection,
    conversation_ids: Iterable[int] | None,
) -> list[AnalyticMessage]:
    """Load only requested conversations when the caller supplied a selection.

    The common interactive workflow analyses one target chat. Pushing that
    selection into the existing membership SQL avoids reading every unrelated
    message before `_group_messages` discards it. Full-archive callers retain the
    original one-pass load.
    """

    if conversation_ids is None:
        return load_analytic_messages(conn)

    selected = sorted({int(conversation_id) for conversation_id in conversation_ids})
    messages: list[AnalyticMessage] = []
    for conversation_id in selected:
        messages.extend(load_analytic_messages(conn, conversation_id))
    return messages


def conversation_fingerprint(messages: Iterable[AnalyticMessage]) -> str:
    return _membership_adapter.conversation_fingerprint(messages)


def _group_messages(
    messages: Iterable[AnalyticMessage],
    conversation_ids: Iterable[int] | None = None,
) -> dict[int, list[AnalyticMessage]]:
    return _membership_adapter._group_messages(messages, conversation_ids)


def _analyze_grouped(
    grouped: dict[int, list[AnalyticMessage]], config: AnalyticsConfig
) -> list[ConversationAnalytics]:
    return _membership_adapter._analyze_grouped(grouped, config)


def analyze_database(
    conn: sqlite3.Connection,
    config: AnalyticsConfig | None = None,
    conversation_ids: Iterable[int] | None = None,
) -> list[ConversationAnalytics]:
    cfg = config or AnalyticsConfig()
    messages = _load_selected_messages(conn, conversation_ids)
    grouped = _group_messages(messages)
    return _analyze_grouped(grouped, cfg)


def analyze_incremental_database(
    conn: sqlite3.Connection,
    config: AnalyticsConfig | None = None,
    conversation_ids: Iterable[int] | None = None,
) -> list[ConversationAnalytics]:
    """Recompute conversations when data, rules, or A3 provenance changed."""

    cfg = config or AnalyticsConfig()
    messages = _load_selected_messages(conn, conversation_ids)
    grouped = _group_messages(messages)
    previous = _latest_analysis_states_with_processing_run(conn)
    expected_signature = analysis_signature(cfg)
    current_processing_run_id = _latest_completed_processing_run_id(conn)

    changed: dict[int, list[AnalyticMessage]] = {}
    for conversation_id, source in grouped.items():
        current_source_fingerprint = conversation_fingerprint(source)
        if previous.get(conversation_id) != (
            current_source_fingerprint,
            expected_signature,
            current_processing_run_id,
        ):
            changed[conversation_id] = source
    return _analyze_grouped(changed, cfg)
