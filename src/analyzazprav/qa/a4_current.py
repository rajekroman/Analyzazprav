from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import sqlite3
from typing import Any

from .a4 import (
    _actual_change_key,
    _build_turns,
    _change_points,
    _close,
    _daily_metrics,
    _finalize,
    _float_key,
    _issue,
    _participant_metrics,
    _reciprocity,
    _response_key,
)


def _date_text(year: Any, month: Any, day: Any) -> str | None:
    if year is None or month is None or day is None:
        return None
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def _load_source_rows(
    conn: sqlite3.Connection, processing_run_id: int
) -> dict[int, list[dict[str, Any]]]:
    """Load exact A2/A3 membership rows using the A3 resolved sender identity."""

    has_resolution = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='processed_message_resolved_sender'"
    ).fetchone() is not None
    resolved_join = ""
    participant_expr = "am.sender_id"
    if has_resolution:
        resolved_join = """
LEFT JOIN processed_message_resolved_sender AS pmrs
  ON pmrs.processing_run_id=pm.processing_run_id
 AND pmrs.membership_id=pm.membership_id
"""
        participant_expr = "COALESCE(pmrs.resolved_participant_id, am.sender_id)"

    rows = conn.execute(
        f"""SELECT am.membership_id,
                   am.id AS message_id,
                   am.conversation_id,
                   {participant_expr} AS analytic_participant_id,
                   am.sent_at_utc_us,
                   pm.session_id,
                   pm.sequence_number,
                   pm.text_clean,
                   pm.word_count,
                   pm.char_count,
                   pm.question_mark_count,
                   pm.exclamation_mark_count,
                   pm.utc_year, pm.utc_month, pm.utc_day, pm.utc_weekday, pm.utc_hour,
                   pm.local_year, pm.local_month, pm.local_day, pm.local_weekday, pm.local_hour
            FROM analysis_messages AS am
            JOIN processed_message AS pm
              ON pm.processing_run_id=?
             AND pm.membership_id=am.membership_id
             AND pm.message_id=am.id
             AND pm.conversation_id=am.conversation_id
            {resolved_join}
            ORDER BY am.conversation_id, pm.sequence_number, am.membership_id""",
        (processing_run_id,),
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        item = {
            "membership_id": int(row[0]),
            "message_id": int(row[1]),
            "conversation_id": int(row[2]),
            "sender_id": None if row[3] is None else int(row[3]),
            "timestamp_us": None if row[4] is None else int(row[4]),
            "session_id": int(row[5]),
            "sequence_number": int(row[6]),
            "text_clean": str(row[7] or ""),
            "word_count": int(row[8]),
            "character_count": int(row[9]),
            "question_mark_count": int(row[10]),
            "exclamation_mark_count": int(row[11]),
            "utc_date": _date_text(row[12], row[13], row[14]),
            "utc_weekday": None if row[15] is None else int(row[15]),
            "utc_hour": None if row[16] is None else int(row[16]),
            "local_date": _date_text(row[17], row[18], row[19]),
            "local_weekday": None if row[20] is None else int(row[20]),
            "local_hour": None if row[21] is None else int(row[21]),
        }
        grouped[item["conversation_id"]].append(item)
    return grouped


def validate_a4_metrics(database: str | Path) -> dict[str, Any]:
    """Independently recompute release-critical A4 metrics from A2/A3 data.

    Arithmetic helpers live in A7, not A4. Participant attribution follows the
    audited A3 resolved-sender mapping when available; message and membership
    provenance remain the original A2 identities.
    """

    database = Path(database)
    issues: list[dict[str, Any]] = []
    checks: dict[str, Any] = {}
    if not database.is_file():
        _issue(issues, "ERROR", "DATABASE_MISSING", str(database))
        return _finalize(database, issues, checks)

    try:
        conn = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error as exc:
        _issue(issues, "ERROR", "DATABASE_OPEN_FAILED", str(exc))
        return _finalize(database, issues, checks)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        required = {
            "processing_run", "processed_message", "analytics_run",
            "analytics_conversation_summary", "analytics_participant_summary",
            "analytics_response_latency", "analytics_daily_participant",
            "analytics_change_point",
        }
        present = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = sorted(required - present)
        if missing:
            _issue(issues, "ERROR", "A4_TABLES_MISSING", ", ".join(missing))
            return _finalize(database, issues, checks)
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='view' AND name='analysis_a4_latest_conversation_run'"
        ).fetchone() is None:
            _issue(issues, "ERROR", "A4_LATEST_VIEW_MISSING", "analysis_a4_latest_conversation_run")
            return _finalize(database, issues, checks)

        latest_a3_row = conn.execute(
            "SELECT id FROM processing_run WHERE status='completed' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if latest_a3_row is None:
            _issue(issues, "ERROR", "A3_COMPLETED_RUN_MISSING", "no completed A3 run")
            return _finalize(database, issues, checks)
        latest_a3 = int(latest_a3_row[0])
        checks["latest_a3_processing_run_id"] = latest_a3
        checks["uses_resolved_participant_identity"] = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='processed_message_resolved_sender'"
        ).fetchone() is not None

        source_by_conversation = _load_source_rows(conn, latest_a3)
        latest_a4_rows = list(
            conn.execute(
                """SELECT latest.conversation_id, latest.analytics_run_id,
                          ar.processing_run_id, ar.config_json
                   FROM analysis_a4_latest_conversation_run AS latest
                   JOIN analytics_run AS ar ON ar.id=latest.analytics_run_id
                   ORDER BY latest.conversation_id"""
            )
        )
        expected_conversations = set(source_by_conversation)
        actual_conversations = {int(row["conversation_id"]) for row in latest_a4_rows}
        checks["a3_conversation_count"] = len(expected_conversations)
        checks["a4_conversation_count"] = len(actual_conversations)
        if actual_conversations != expected_conversations:
            _issue(
                issues, "ERROR", "A4_CONVERSATION_COVERAGE_MISMATCH",
                f"missing={sorted(expected_conversations-actual_conversations)}, extra={sorted(actual_conversations-expected_conversations)}",
            )

        oracle_response_count = 0
        oracle_change_count = 0
        checked_participants = 0
        for state in latest_a4_rows:
            conversation_id = int(state["conversation_id"])
            analytics_run_id = int(state["analytics_run_id"])
            if int(state["processing_run_id"]) != latest_a3:
                _issue(
                    issues, "ERROR", "A4_STALE_A3_PROVENANCE",
                    f"conversation={conversation_id}, A4 processing_run={state['processing_run_id']}, latest A3={latest_a3}",
                )
                continue
            try:
                config = json.loads(state["config_json"] or "{}")
            except json.JSONDecodeError as exc:
                _issue(issues, "ERROR", "A4_CONFIG_JSON_INVALID", f"conversation={conversation_id}: {exc}")
                continue
            if not isinstance(config, dict):
                _issue(issues, "ERROR", "A4_CONFIG_JSON_INVALID", f"conversation={conversation_id}: config is not object")
                continue

            rows = source_by_conversation.get(conversation_id, [])
            turns = _build_turns(rows)
            responses = __import__("analyzazprav.qa.a4", fromlist=["_responses"])._responses(turns)
            participants = _participant_metrics(rows, turns, responses, config)
            daily = _daily_metrics(rows, turns, responses, config)
            expected_changes = Counter(_change_points(conversation_id, daily, config))
            oracle_response_count += len(responses)
            oracle_change_count += sum(expected_changes.values())
            checked_participants += len(participants)

            expected_summary = {
                "source_message_count": len(rows),
                "known_sender_message_count": sum(row["sender_id"] is not None for row in rows),
                "unknown_sender_message_count": sum(row["sender_id"] is None for row in rows),
                "turn_count": len(turns),
                "session_count": len({int(row["session_id"]) for row in rows}),
                "message_reciprocity": _reciprocity(participants, "message_count"),
                "word_reciprocity": _reciprocity(participants, "word_count"),
                "turn_reciprocity": _reciprocity(participants, "turn_count"),
                "initiation_reciprocity": _reciprocity(participants, "initiations"),
            }
            actual_summary = conn.execute(
                "SELECT * FROM analytics_conversation_summary WHERE analytics_run_id=? AND conversation_id=?",
                (analytics_run_id, conversation_id),
            ).fetchone()
            if actual_summary is None:
                _issue(issues, "ERROR", "A4_CONVERSATION_SUMMARY_MISSING", f"conversation={conversation_id}")
            else:
                for field, expected in expected_summary.items():
                    actual = actual_summary[field]
                    same = _close(actual, expected) if isinstance(expected, float) or expected is None else actual == expected
                    if not same:
                        _issue(issues, "ERROR", "A4_CONVERSATION_SUMMARY_MISMATCH", f"conversation={conversation_id}, field={field}, expected={expected!r}, actual={actual!r}")

            actual_participants = {
                int(row["participant_id"]): row
                for row in conn.execute(
                    "SELECT * FROM analytics_participant_summary WHERE analytics_run_id=? AND conversation_id=?",
                    (analytics_run_id, conversation_id),
                )
            }
            if set(actual_participants) != set(participants):
                _issue(issues, "ERROR", "A4_PARTICIPANT_SET_MISMATCH", f"conversation={conversation_id}, expected={sorted(participants)}, actual={sorted(actual_participants)}")
            participant_fields = (
                "message_count", "word_count", "character_count", "active_days",
                "turn_count", "initiations", "initiation_share", "question_count",
                "exclamation_count", "affection_marker_count", "negative_marker_count",
                "response_turn_count", "latency_sample_count", "unanswered_turn_count",
                "mean_response_latency_seconds", "median_response_latency_seconds",
                "p25_response_latency_seconds", "p75_response_latency_seconds",
                "p90_response_latency_seconds", "median_response_effort_ratio", "engagement_score",
            )
            for pid, expected in participants.items():
                actual = actual_participants.get(pid)
                if actual is None:
                    continue
                for field in participant_fields:
                    expected_value = expected[field]
                    actual_value = actual[field]
                    if isinstance(expected_value, float) or expected_value is None:
                        same = _close(actual_value, expected_value, 1e-6 if field == "engagement_score" else 1e-9)
                    else:
                        same = actual_value == expected_value
                    if not same:
                        _issue(issues, "ERROR", "A4_PARTICIPANT_METRIC_MISMATCH", f"conversation={conversation_id}, participant={pid}, field={field}, expected={expected_value!r}, actual={actual_value!r}")

            expected_responses = Counter(_response_key(sample) for sample in responses)
            actual_responses = Counter(
                (
                    int(row["conversation_id"]), int(row["session_id"]),
                    int(row["from_participant_id"]), int(row["responder_id"]),
                    int(row["previous_turn_id"]), int(row["response_turn_id"]),
                    _float_key(row["latency_seconds"]), _float_key(row["response_effort_ratio"]),
                )
                for row in conn.execute(
                    "SELECT * FROM analytics_response_latency WHERE analytics_run_id=? AND conversation_id=?",
                    (analytics_run_id, conversation_id),
                )
            )
            if actual_responses != expected_responses:
                _issue(issues, "ERROR", "A4_RESPONSE_SAMPLE_MISMATCH", f"conversation={conversation_id}, expected={list(expected_responses.elements())[:5]}, actual={list(actual_responses.elements())[:5]}")

            expected_daily = {(int(row["participant_id"]), str(row["period_date"])): row for row in daily}
            actual_daily = {
                (int(row["participant_id"]), str(row["period_date"])): row
                for row in conn.execute(
                    "SELECT * FROM analytics_daily_participant WHERE analytics_run_id=? AND conversation_id=?",
                    (analytics_run_id, conversation_id),
                )
            }
            if set(expected_daily) != set(actual_daily):
                _issue(issues, "ERROR", "A4_DAILY_SET_MISMATCH", f"conversation={conversation_id}, expected_rows={len(expected_daily)}, actual_rows={len(actual_daily)}")
            daily_fields = (
                "date_basis", "message_count", "word_count", "turn_count", "initiations",
                "question_count", "affection_marker_count", "negative_marker_count",
                "median_response_latency_seconds", "median_response_effort_ratio",
            )
            for key, expected in expected_daily.items():
                actual = actual_daily.get(key)
                if actual is None:
                    continue
                for field in daily_fields:
                    expected_value = expected[field]
                    actual_value = actual[field]
                    same = _close(actual_value, expected_value) if isinstance(expected_value, float) or expected_value is None else actual_value == expected_value
                    if not same:
                        _issue(issues, "ERROR", "A4_DAILY_METRIC_MISMATCH", f"conversation={conversation_id}, key={key}, field={field}, expected={expected_value!r}, actual={actual_value!r}")
                try:
                    actual_ids = tuple(int(value) for value in json.loads(actual["source_message_ids_json"] or "[]"))
                except (json.JSONDecodeError, TypeError, ValueError):
                    actual_ids = ("__invalid_json__",)
                if actual_ids != tuple(expected["source_message_ids"]):
                    _issue(issues, "ERROR", "A4_DAILY_EVIDENCE_MISMATCH", f"conversation={conversation_id}, key={key}, expected={expected['source_message_ids']}, actual={actual_ids}")

            actual_changes = Counter(
                _actual_change_key(row)
                for row in conn.execute(
                    "SELECT * FROM analytics_change_point WHERE analytics_run_id=? AND conversation_id=?",
                    (analytics_run_id, conversation_id),
                )
            )
            if actual_changes != expected_changes:
                _issue(
                    issues, "ERROR", "A4_CHANGE_POINT_MISMATCH",
                    f"conversation={conversation_id}, missing={list((expected_changes-actual_changes).elements())[:5]}, extra={list((actual_changes-expected_changes).elements())[:5]}",
                )

        checks["oracle_participant_count"] = checked_participants
        checks["oracle_response_sample_count"] = oracle_response_count
        checks["oracle_change_point_count"] = oracle_change_count
        checks["sqlite_integrity"] = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        checks["foreign_key_error_count"] = len(conn.execute("PRAGMA foreign_key_check").fetchall())
        if checks["sqlite_integrity"] != "ok":
            _issue(issues, "ERROR", "SQLITE_INTEGRITY_FAILED", checks["sqlite_integrity"])
        if checks["foreign_key_error_count"]:
            _issue(issues, "ERROR", "FOREIGN_KEY_ERRORS", str(checks["foreign_key_error_count"]))
    except (sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        _issue(issues, "ERROR", "A4_ORACLE_QUERY_FAILED", str(exc))
    finally:
        conn.close()
    return _finalize(database, issues, checks)
