from __future__ import annotations

import sqlite3
from typing import Any

from .database import CanonicalDatabase


_REQUIRED_TABLES = {
    "import_run",
    "conversation",
    "conversation_source",
    "message",
    "message_source",
    "message_conversation",
    "message_source_conversation",
    "message_relation",
    "message_relation_source",
    "attachment",
    "message_attachment",
    "message_attachment_occurrence",
    "attachment_source",
}

_REQUIRED_VIEWS = {
    "analysis_messages",
    "analysis_conversations",
    "analysis_attachments",
    "analysis_attachment_sources",
    "analysis_message_sources",
    "analysis_message_memberships",
    "analysis_message_relation_sources",
}


def _scalar(db: CanonicalDatabase, sql: str) -> int:
    return int(db.conn.execute(sql).fetchone()[0])


def _record_count_error(
    errors: list[dict[str, Any]],
    checks: dict[str, Any],
    *,
    key: str,
    code: str,
    count: int,
    detail: str,
) -> None:
    checks[key] = count
    if count:
        errors.append({"code": code, "count": count, "detail": detail})


def _record_mismatch(
    errors: list[dict[str, Any]],
    checks: dict[str, Any],
    *,
    key: str,
    code: str,
    actual: int,
    expected: int,
    detail: str,
) -> None:
    checks[key] = {"actual": actual, "expected": expected}
    if actual != expected:
        errors.append(
            {
                "code": code,
                "count": abs(actual - expected),
                "actual": actual,
                "expected": expected,
                "detail": detail,
            }
        )


def full_integrity_report(db: CanonicalDatabase) -> dict[str, Any]:
    """Return structural SQLite checks plus current A2 semantic invariants.

    SQLite foreign keys prove that referenced rows exist. They do not prove that
    source provenance and canonical memberships/relations describe the same
    logical records, nor that analytical views cover every authoritative row.
    These checks close that gap without performing repair or fuzzy inference.
    """

    base = db.integrity_report()
    checks: dict[str, Any] = {}
    semantic_errors: list[dict[str, Any]] = []

    objects = {
        str(row["name"]): str(row["type"])
        for row in db.conn.execute(
            "SELECT name, type FROM sqlite_master WHERE type IN ('table','view')"
        )
    }
    tables = {name for name, kind in objects.items() if kind == "table"}
    views = {name for name, kind in objects.items() if kind == "view"}
    missing_tables = sorted(_REQUIRED_TABLES - tables)
    missing_views = sorted(_REQUIRED_VIEWS - views)
    checks["missing_required_tables"] = missing_tables
    checks["missing_required_views"] = missing_views
    if missing_tables:
        semantic_errors.append(
            {
                "code": "A2_REQUIRED_TABLES_MISSING",
                "count": len(missing_tables),
                "items": missing_tables,
                "detail": "Required current A2 tables are missing.",
            }
        )
    if missing_views:
        semantic_errors.append(
            {
                "code": "A2_REQUIRED_VIEWS_MISSING",
                "count": len(missing_views),
                "items": missing_views,
                "detail": "Required current A2 analytical views are missing.",
            }
        )

    if not missing_tables and not missing_views:
        try:
            _record_count_error(
                semantic_errors,
                checks,
                key="messages_without_source",
                code="MESSAGE_SOURCE_TRACE_MISSING",
                count=_scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM message m
                    WHERE NOT EXISTS (
                        SELECT 1 FROM message_source ms WHERE ms.message_id = m.id
                    )
                    """,
                ),
                detail="Canonical message has no message_source provenance.",
            )
            messages_without_membership = _scalar(
                db,
                """
                SELECT COUNT(*)
                FROM message m
                WHERE NOT EXISTS (
                    SELECT 1 FROM message_conversation mc WHERE mc.message_id = m.id
                )
                """,
            )
            _record_count_error(
                semantic_errors,
                checks,
                key="messages_without_membership",
                code="MESSAGE_CONVERSATION_MEMBERSHIP_MISSING",
                count=messages_without_membership,
                detail="Canonical message has no message_conversation membership.",
            )
            _record_count_error(
                semantic_errors,
                checks,
                key="messages_with_invalid_primary_membership_count",
                code="MESSAGE_PRIMARY_MEMBERSHIP_COUNT_INVALID",
                count=_scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM (
                        SELECT m.id
                        FROM message m
                        LEFT JOIN message_conversation mc ON mc.message_id = m.id
                        GROUP BY m.id
                        HAVING SUM(CASE WHEN mc.is_primary = 1 THEN 1 ELSE 0 END) <> 1
                    )
                    """,
                ),
                detail="Each canonical message must have exactly one primary membership.",
            )
            _record_count_error(
                semantic_errors,
                checks,
                key="message_primary_pointer_mismatches",
                code="MESSAGE_PRIMARY_POINTER_MISMATCH",
                count=_scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM message m
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM message_conversation mc
                        WHERE mc.message_id = m.id
                          AND mc.is_primary = 1
                          AND mc.conversation_id = m.conversation_id
                    )
                    """,
                ),
                detail="message.conversation_id must equal the primary membership conversation.",
            )
            _record_count_error(
                semantic_errors,
                checks,
                key="message_sources_without_source_conversation_relation",
                code="MESSAGE_SOURCE_CONVERSATION_TRACE_MISSING",
                count=_scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM message_source ms
                    WHERE ms.source_conversation_id IS NOT NULL
                      AND trim(ms.source_conversation_id) <> ''
                      AND NOT EXISTS (
                          SELECT 1
                          FROM message_source_conversation msc
                          WHERE msc.message_source_id = ms.id
                      )
                    """,
                ),
                detail="Source message names a source conversation but has no preserved source relation.",
            )
            _record_count_error(
                semantic_errors,
                checks,
                key="message_source_membership_message_mismatches",
                code="MESSAGE_SOURCE_MEMBERSHIP_MESSAGE_MISMATCH",
                count=_scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM message_source_conversation msc
                    JOIN message_source ms ON ms.id = msc.message_source_id
                    JOIN message_conversation mc ON mc.id = msc.membership_id
                    WHERE ms.message_id <> mc.message_id
                    """,
                ),
                detail="Source relation points to a membership belonging to a different canonical message.",
            )
            _record_count_error(
                semantic_errors,
                checks,
                key="source_membership_conversation_mismatches",
                code="SOURCE_MEMBERSHIP_CONVERSATION_MISMATCH",
                count=_scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM message_source_conversation msc
                    JOIN conversation_source cs ON cs.id = msc.conversation_source_id
                    JOIN message_conversation mc ON mc.id = msc.membership_id
                    WHERE cs.conversation_id <> mc.conversation_id
                    """,
                ),
                detail="Source conversation relation points to a membership for a different canonical conversation.",
            )
            _record_count_error(
                semantic_errors,
                checks,
                key="message_source_import_type_mismatches",
                code="MESSAGE_SOURCE_IMPORT_TYPE_MISMATCH",
                count=_scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM message_source ms
                    JOIN import_run ir ON ir.id = ms.import_run_id
                    WHERE ms.source_type <> ir.source_type
                    """,
                ),
                detail="message_source.source_type disagrees with its import_run source_type.",
            )
            _record_count_error(
                semantic_errors,
                checks,
                key="conversation_source_import_type_mismatches",
                code="CONVERSATION_SOURCE_IMPORT_TYPE_MISMATCH",
                count=_scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM conversation_source cs
                    JOIN import_run ir ON ir.id = cs.import_run_id
                    WHERE cs.source_type <> ir.source_type
                    """,
                ),
                detail="conversation_source.source_type disagrees with its import_run source_type.",
            )
            _record_count_error(
                semantic_errors,
                checks,
                key="conversation_source_sha_mismatches",
                code="CONVERSATION_SOURCE_SHA_MISMATCH",
                count=_scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM conversation_source cs
                    JOIN import_run ir ON ir.id = cs.import_run_id
                    WHERE cs.source_sha256 IS NOT NULL
                      AND ir.source_sha256 IS NOT NULL
                      AND cs.source_sha256 <> ir.source_sha256
                    """,
                ),
                detail="conversation_source source SHA disagrees with its import_run raw-source SHA.",
            )

            _record_count_error(
                semantic_errors,
                checks,
                key="resolved_relation_source_message_mismatches",
                code="RELATION_SOURCE_MESSAGE_MISMATCH",
                count=_scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM message_relation_source mrs
                    JOIN message_source ms ON ms.id = mrs.message_source_id
                    JOIN message_relation mr ON mr.id = mrs.canonical_relation_id
                    WHERE mrs.canonical_relation_id IS NOT NULL
                      AND ms.message_id <> mr.source_message_id
                    """,
                ),
                detail="Resolved source relation is linked to a canonical relation owned by a different source message.",
            )
            _record_count_error(
                semantic_errors,
                checks,
                key="resolved_relation_type_mismatches",
                code="RELATION_SOURCE_TYPE_MISMATCH",
                count=_scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM message_relation_source mrs
                    JOIN message_relation mr ON mr.id = mrs.canonical_relation_id
                    WHERE mrs.canonical_relation_id IS NOT NULL
                      AND mrs.relation_type <> mr.relation_type
                    """,
                ),
                detail="Resolved source relation type disagrees with its canonical message_relation type.",
            )
            _record_count_error(
                semantic_errors,
                checks,
                key="resolved_guid_relation_target_mismatches",
                code="RELATION_SOURCE_TARGET_MISMATCH",
                count=_scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM message_relation_source mrs
                    JOIN message_relation mr ON mr.id = mrs.canonical_relation_id
                    JOIN message target ON target.id = mr.target_message_id
                    WHERE mrs.canonical_relation_id IS NOT NULL
                      AND mrs.target_identifier_type = 'guid'
                      AND (
                          target.canonical_guid IS NOT mrs.target_identifier_value
                          OR target.service IS NOT mrs.target_service
                      )
                    """,
                ),
                detail="Resolved GUID source relation disagrees with the canonical target GUID/service identity.",
            )

            _record_count_error(
                semantic_errors,
                checks,
                key="attachments_without_source",
                code="ATTACHMENT_SOURCE_TRACE_MISSING",
                count=_scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM attachment a
                    WHERE NOT EXISTS (
                        SELECT 1 FROM attachment_source s WHERE s.attachment_id = a.id
                    )
                    """,
                ),
                detail="Canonical attachment has no attachment_source provenance.",
            )
            _record_count_error(
                semantic_errors,
                checks,
                key="attachment_occurrences_without_blob_mapping",
                code="ATTACHMENT_OCCURRENCE_BLOB_MAPPING_MISSING",
                count=_scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM message_attachment_occurrence mao
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM message_attachment ma
                        WHERE ma.message_id = mao.message_id
                          AND ma.attachment_id = mao.attachment_id
                    )
                    """,
                ),
                detail="Attachment occurrence is not backed by the canonical message_attachment mapping.",
            )
            _record_count_error(
                semantic_errors,
                checks,
                key="attachment_occurrences_without_source",
                code="ATTACHMENT_OCCURRENCE_SOURCE_TRACE_MISSING",
                count=_scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM message_attachment_occurrence mao
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM attachment_source s
                        WHERE s.message_attachment_occurrence_id = mao.id
                    )
                    """,
                ),
                detail="Attachment occurrence has no source provenance row.",
            )
            _record_count_error(
                semantic_errors,
                checks,
                key="attachment_source_occurrence_attachment_mismatches",
                code="ATTACHMENT_SOURCE_OCCURRENCE_MISMATCH",
                count=_scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM attachment_source s
                    JOIN message_attachment_occurrence mao
                      ON mao.id = s.message_attachment_occurrence_id
                    WHERE s.attachment_id <> mao.attachment_id
                    """,
                ),
                detail="attachment_source and linked occurrence reference different canonical attachments.",
            )
            _record_count_error(
                semantic_errors,
                checks,
                key="source_occurrence_keys_without_occurrence",
                code="ATTACHMENT_SOURCE_OCCURRENCE_LINK_MISSING",
                count=_scalar(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM attachment_source s
                    WHERE s.source_occurrence_key IS NOT NULL
                      AND s.message_attachment_occurrence_id IS NULL
                    """,
                ),
                detail="Source occurrence identity exists without a canonical occurrence link.",
            )

            message_count = _scalar(db, "SELECT COUNT(*) FROM message")
            membership_count = _scalar(db, "SELECT COUNT(*) FROM message_conversation")
            occurrence_count = _scalar(db, "SELECT COUNT(*) FROM message_attachment_occurrence")
            attachment_source_count = _scalar(db, "SELECT COUNT(*) FROM attachment_source")
            source_count = _scalar(db, "SELECT COUNT(*) FROM message_source")
            relation_source_count = _scalar(db, "SELECT COUNT(*) FROM message_relation_source")
            conversation_count = _scalar(db, "SELECT COUNT(*) FROM conversation")

            _record_mismatch(
                semantic_errors,
                checks,
                key="analysis_messages_vs_memberships",
                code="ANALYSIS_MESSAGES_MEMBERSHIP_COUNT_MISMATCH",
                actual=_scalar(db, "SELECT COUNT(*) FROM analysis_messages"),
                expected=membership_count,
                detail="analysis_messages must expose exactly one row per canonical message membership.",
            )
            _record_mismatch(
                semantic_errors,
                checks,
                key="analysis_messages_distinct_ids_vs_messages",
                code="ANALYSIS_MESSAGES_CANONICAL_COVERAGE_MISMATCH",
                actual=_scalar(db, "SELECT COUNT(DISTINCT id) FROM analysis_messages"),
                expected=message_count,
                detail="Every canonical message must be represented by the membership-aware analysis view.",
            )
            _record_mismatch(
                semantic_errors,
                checks,
                key="analysis_conversations_vs_conversations",
                code="ANALYSIS_CONVERSATIONS_COUNT_MISMATCH",
                actual=_scalar(db, "SELECT COUNT(*) FROM analysis_conversations"),
                expected=conversation_count,
                detail="analysis_conversations must expose every canonical conversation exactly once.",
            )
            _record_mismatch(
                semantic_errors,
                checks,
                key="analysis_attachments_vs_occurrences",
                code="ANALYSIS_ATTACHMENTS_OCCURRENCE_COUNT_MISMATCH",
                actual=_scalar(db, "SELECT COUNT(*) FROM analysis_attachments"),
                expected=occurrence_count,
                detail="analysis_attachments must expose attachment occurrences, not only unique blobs.",
            )
            _record_mismatch(
                semantic_errors,
                checks,
                key="analysis_attachment_sources_vs_sources",
                code="ANALYSIS_ATTACHMENT_SOURCES_COUNT_MISMATCH",
                actual=_scalar(db, "SELECT COUNT(*) FROM analysis_attachment_sources"),
                expected=attachment_source_count,
                detail="analysis_attachment_sources must expose every attachment_source provenance row.",
            )
            _record_mismatch(
                semantic_errors,
                checks,
                key="analysis_message_sources_vs_sources",
                code="ANALYSIS_MESSAGE_SOURCES_COUNT_MISMATCH",
                actual=_scalar(db, "SELECT COUNT(*) FROM analysis_message_sources"),
                expected=source_count,
                detail="analysis_message_sources must expose every message_source provenance row.",
            )
            _record_mismatch(
                semantic_errors,
                checks,
                key="analysis_membership_coverage",
                code="ANALYSIS_MEMBERSHIP_COVERAGE_MISMATCH",
                actual=_scalar(
                    db,
                    "SELECT COUNT(DISTINCT membership_id) FROM analysis_message_memberships",
                ),
                expected=membership_count,
                detail="analysis_message_memberships must cover every canonical membership.",
            )
            _record_mismatch(
                semantic_errors,
                checks,
                key="analysis_relation_sources_vs_sources",
                code="ANALYSIS_RELATION_SOURCES_COUNT_MISMATCH",
                actual=_scalar(db, "SELECT COUNT(*) FROM analysis_message_relation_sources"),
                expected=relation_source_count,
                detail="analysis_message_relation_sources must expose every message_relation_source provenance row.",
            )
        except sqlite3.Error as exc:
            semantic_errors.append(
                {
                    "code": "A2_SEMANTIC_INTEGRITY_QUERY_FAILED",
                    "count": 1,
                    "detail": str(exc),
                }
            )

    ok = (
        base.get("integrity") == "ok"
        and not base.get("foreign_key_errors")
        and not semantic_errors
    )
    return {
        **base,
        "checks": checks,
        "semantic_errors": semantic_errors,
        "ok": ok,
    }
