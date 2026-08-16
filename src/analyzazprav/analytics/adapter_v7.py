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


def _resolve_participants(
    conn: sqlite3.Connection, messages: list[AnalyticMessage]
) -> list[AnalyticMessage]:
    """Replace raw A2 sender IDs with audited A3 v5 resolved-person IDs.

    Legacy/minimal calculation fixtures that do not contain A3 v5 participant
    sidecars keep their raw sender IDs. A real A3 v5 database is fail-closed:
    once the v5 tables exist, the resolved latest-view and exact membership
    coverage are required.
    """

    has_v5_sidecars = _object_exists(conn, "resolved_participant", "table")
    if not has_v5_sidecars:
        return messages

    if not _object_exists(conn, "analysis_processed_messages_resolved_latest", "view"):
        raise RuntimeError(
            "A4 requires A3 v5 analysis_processed_messages_resolved_latest when "
            "participant-resolution sidecars are present"
        )

    rows = list(
        conn.execute(
            """SELECT membership_id, message_id, conversation_id, resolved_sender_id
               FROM analysis_processed_messages_resolved_latest"""
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
    grouped = _group_messages(load_analytic_messages(conn), conversation_ids)
    return _analyze_grouped(grouped, cfg)


def analyze_incremental_database(
    conn: sqlite3.Connection,
    config: AnalyticsConfig | None = None,
    conversation_ids: Iterable[int] | None = None,
) -> list[ConversationAnalytics]:
    cfg = config or AnalyticsConfig()
    grouped = _group_messages(load_analytic_messages(conn), conversation_ids)
    previous = _membership_adapter._latest_analysis_states(conn)
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
