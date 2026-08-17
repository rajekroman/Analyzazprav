from __future__ import annotations

import sqlite3
from typing import Any

from .database import CanonicalDatabase
from .integrity import full_integrity_report as _base_integrity_report


def _schema_version(db: CanonicalDatabase) -> int:
    row = db.conn.execute(
        "SELECT value FROM schema_meta WHERE key='schema_version'"
    ).fetchone()
    if row is None:
        return 0
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return 0


def _count(db: CanonicalDatabase, sql: str) -> int:
    return int(db.conn.execute(sql).fetchone()[0])


def _record_error(
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


def full_integrity_report(db: CanonicalDatabase) -> dict[str, Any]:
    """Extend the stable A2 semantic report with schema-v7 relation provenance.

    Schema v7 is append-only. Databases below v7 keep the previous report exactly;
    v7 additionally validates the source-relation objects and cross-row semantics
    that ordinary SQLite foreign keys cannot express.
    """

    report = _base_integrity_report(db)
    if _schema_version(db) < 7:
        return report

    checks = dict(report.get("checks") or {})
    semantic_errors = list(report.get("semantic_errors") or [])

    objects = {
        (str(row["type"]), str(row["name"]))
        for row in db.conn.execute(
            "SELECT type, name FROM sqlite_master WHERE type IN ('table','view')"
        )
    }
    required = {
        ("table", "message_relation_source"),
        ("view", "analysis_message_relation_sources"),
    }
    missing = sorted(f"{kind}:{name}" for kind, name in required - objects)
    checks["v7_missing_relation_objects"] = missing
    if missing:
        semantic_errors.append(
            {
                "code": "A2_V7_RELATION_OBJECTS_MISSING",
                "count": len(missing),
                "items": missing,
                "detail": "Schema v7 requires source-relation provenance table and stable analysis view.",
            }
        )
    else:
        try:
            _record_error(
                semantic_errors,
                checks,
                key="relation_source_import_mismatches",
                code="RELATION_SOURCE_IMPORT_MISMATCH",
                count=_count(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM message_relation_source mrs
                    JOIN message_source ms ON ms.id=mrs.message_source_id
                    WHERE mrs.import_run_id <> ms.import_run_id
                    """,
                ),
                detail="message_relation_source import_run_id must equal its message_source import run.",
            )
            _record_error(
                semantic_errors,
                checks,
                key="relation_sources_from_uncompleted_runs",
                code="RELATION_SOURCE_RUN_NOT_COMPLETED",
                count=_count(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM message_relation_source mrs
                    JOIN import_run ir ON ir.id=mrs.import_run_id
                    WHERE ir.status <> 'completed'
                    """,
                ),
                detail="Committed source-relation provenance may only belong to completed imports.",
            )
            _record_error(
                semantic_errors,
                checks,
                key="relation_sources_with_empty_target_guid",
                code="RELATION_SOURCE_TARGET_GUID_EMPTY",
                count=_count(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM message_relation_source
                    WHERE target_source_guid IS NULL OR target_source_guid = ''
                    """,
                ),
                detail="Current schema-v7 source relations are GUID-targeted and require the exact source GUID.",
            )
            _record_error(
                semantic_errors,
                checks,
                key="resolved_relation_source_semantic_mismatches",
                code="RELATION_SOURCE_CANONICAL_MISMATCH",
                count=_count(
                    db,
                    """
                    SELECT COUNT(*)
                    FROM message_relation_source mrs
                    JOIN message_source ms ON ms.id=mrs.message_source_id
                    LEFT JOIN message_relation mr ON mr.id=mrs.resolved_relation_id
                    LEFT JOIN message target ON target.id=mr.target_message_id
                    WHERE mrs.resolution_status='resolved'
                      AND (
                          mr.id IS NULL
                          OR mr.source_message_id <> ms.message_id
                          OR mr.relation_type <> mrs.relation_type
                          OR NOT (target.canonical_guid IS mrs.target_source_guid)
                          OR NOT (target.service IS mrs.target_service)
                      )
                    """,
                ),
                detail=(
                    "Resolved source relation must point to a canonical relation with the same "
                    "source message, relation type, exact target GUID and exact target service."
                ),
            )

            physical = _count(db, "SELECT COUNT(*) FROM message_relation_source")
            projected = _count(db, "SELECT COUNT(*) FROM analysis_message_relation_sources")
            checks["analysis_relation_sources_vs_physical"] = {
                "actual": projected,
                "expected": physical,
            }
            if projected != physical:
                semantic_errors.append(
                    {
                        "code": "ANALYSIS_RELATION_SOURCE_COVERAGE_MISMATCH",
                        "count": abs(projected - physical),
                        "actual": projected,
                        "expected": physical,
                        "detail": (
                            "analysis_message_relation_sources must expose every physical "
                            "message_relation_source provenance row exactly once."
                        ),
                    }
                )
        except sqlite3.Error as exc:
            semantic_errors.append(
                {
                    "code": "A2_V7_RELATION_INTEGRITY_QUERY_FAILED",
                    "count": 1,
                    "detail": str(exc),
                }
            )

    ok = (
        report.get("integrity") == "ok"
        and not report.get("foreign_key_errors")
        and not semantic_errors
    )
    return {
        **report,
        "checks": checks,
        "semantic_errors": semantic_errors,
        "ok": ok,
    }
