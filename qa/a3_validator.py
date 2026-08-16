from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

STATUS_PASS = "PASS"
STATUS_WARNING = "WARNING"
STATUS_FAIL = "FAIL"

REQUIRED_TABLES = {
    "processing_run",
    "processed_message",
    "sender_run",
    "conversation_session",
    "conversation_thread",
    "conversation_thread_message",
    "a3_duplicate_candidate",
    "message_conversation",
    "message_source",
    "message",
}


def _issue(issues: list[dict[str, Any]], severity: str, code: str, detail: str) -> None:
    issues.append({"severity": severity, "code": code, "detail": detail})


def _source_sort_data(conn: sqlite3.Connection) -> dict[int, tuple[int | None, str | None]]:
    """Independently derive the source ordering fields consumed by A3.

    A3 uses the first non-null source_message_id in deterministic provenance
    order and the minimum numeric source_row_id across all source occurrences.
    """

    grouped: dict[int, list[tuple[str | None, int | None]]] = defaultdict(list)
    for row in conn.execute(
        """SELECT message_id, source_message_id, source_row_id
           FROM message_source
           ORDER BY message_id,
                    CASE WHEN source_record_key IS NULL THEN 1 ELSE 0 END,
                    source_record_key, id"""
    ):
        try:
            source_order = None if row[2] is None else int(row[2])
        except (TypeError, ValueError):
            source_order = None
        grouped[int(row[0])].append(
            (None if row[1] is None else str(row[1]), source_order)
        )

    result: dict[int, tuple[int | None, str | None]] = {}
    for message_id, values in grouped.items():
        source_message_id = next((item[0] for item in values if item[0] is not None), None)
        orders = [item[1] for item in values if item[1] is not None]
        result[message_id] = (min(orders) if orders else None, source_message_id)
    return result


def _canonical_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    source_data = _source_sort_data(conn)
    rows: list[dict[str, Any]] = []
    for row in conn.execute(
        """SELECT mc.id, mc.message_id, mc.conversation_id,
                  m.sender_id, m.sent_at_utc_us
           FROM message_conversation mc
           JOIN message m ON m.id=mc.message_id"""
    ):
        message_id = int(row[1])
        source_order, source_message_id = source_data.get(message_id, (None, None))
        rows.append(
            {
                "membership_id": int(row[0]),
                "message_id": message_id,
                "conversation_id": int(row[2]),
                "sender_id": None if row[3] is None else int(row[3]),
                "timestamp_us": None if row[4] is None else int(row[4]),
                "source_order": source_order,
                "source_message_id": source_message_id,
            }
        )
    return rows


def _sort_key(row: dict[str, Any]) -> tuple[int, int, int, str, int, int]:
    maximum = 2**63 - 1
    timestamp = row["timestamp_us"]
    source_order = row["source_order"]
    return (
        1 if timestamp is None else 0,
        timestamp if timestamp is not None else maximum,
        source_order if source_order is not None else maximum,
        row["source_message_id"] or "",
        row["message_id"],
        row["membership_id"],
    )


def _grouped_order(canonical: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in canonical:
        grouped[row["conversation_id"]].append(row)
    for rows in grouped.values():
        rows.sort(key=_sort_key)
    return dict(grouped)


def _expected_sessions(
    grouped: dict[int, list[dict[str, Any]]], gap_threshold_us: int
) -> list[list[int]]:
    sessions: list[list[int]] = []
    for conversation_id in sorted(grouped):
        rows = grouped[conversation_id]
        start = 0
        for index in range(1, len(rows) + 1):
            boundary = index == len(rows)
            if not boundary:
                previous = rows[index - 1]["timestamp_us"]
                current = rows[index]["timestamp_us"]
                boundary = (
                    previous is None
                    or current is None
                    or current - previous > gap_threshold_us
                )
            if boundary:
                chunk = rows[start:index]
                if chunk:
                    sessions.append([row["membership_id"] for row in chunk])
                start = index
    return sessions


def _expected_sender_runs(grouped: dict[int, list[dict[str, Any]]]) -> list[list[int]]:
    runs: list[list[int]] = []
    for conversation_id in sorted(grouped):
        rows = grouped[conversation_id]
        start = 0
        while start < len(rows):
            sender = rows[start]["sender_id"]
            end = start + 1
            while end < len(rows) and rows[end]["sender_id"] == sender:
                end += 1
            runs.append([row["membership_id"] for row in rows[start:end]])
            start = end
    return runs


def validate_a3_database(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    issues: list[dict[str, Any]] = []
    checks: dict[str, Any] = {}
    if not path.is_file():
        _issue(issues, "ERROR", "DATABASE_MISSING", f"SQLite database not found: {path}")
        return _finalize(path, issues, checks)

    uri = f"{path.resolve().as_uri()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
    except sqlite3.Error as exc:
        _issue(issues, "ERROR", "DATABASE_OPEN_FAILED", str(exc))
        return _finalize(path, issues, checks)

    try:
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = sorted(REQUIRED_TABLES - tables)
        if missing:
            _issue(issues, "ERROR", "A3_REQUIRED_TABLES_MISSING", ", ".join(missing))
            return _finalize(path, issues, checks)

        fk = list(conn.execute("PRAGMA foreign_key_check"))
        checks["foreign_key_errors"] = len(fk)
        if fk:
            _issue(
                issues,
                "ERROR",
                "SQLITE_FOREIGN_KEY_ERRORS",
                f"{len(fk)} foreign-key violation(s)",
            )

        run = conn.execute(
            """SELECT id,processing_version,status,config_json,input_membership_count,
                      canonical_message_count,output_membership_count
               FROM processing_run WHERE status='completed' ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        if run is None:
            _issue(
                issues,
                "ERROR",
                "A3_COMPLETED_RUN_MISSING",
                "No completed A3 processing_run exists",
            )
            return _finalize(path, issues, checks)

        run_id = int(run["id"])
        checks["processing_run_id"] = run_id
        checks["processing_version"] = str(run["processing_version"])
        try:
            config = json.loads(run["config_json"] or "{}")
        except json.JSONDecodeError:
            config = {}
            _issue(
                issues,
                "ERROR",
                "A3_CONFIG_JSON_INVALID",
                f"processing_run {run_id} has invalid config_json",
            )
        gap_seconds = config.get("session_gap_seconds") if isinstance(config, dict) else None
        if isinstance(gap_seconds, bool) or not isinstance(gap_seconds, int) or gap_seconds <= 0:
            gap_threshold_us = 0
            _issue(
                issues,
                "ERROR",
                "A3_SESSION_GAP_INVALID",
                f"processing_run {run_id} has invalid session_gap_seconds",
            )
        else:
            gap_threshold_us = gap_seconds * 1_000_000

        canonical = _canonical_rows(conn)
        grouped = _grouped_order(canonical)
        canonical_by_membership = {row["membership_id"]: row for row in canonical}
        canonical_memberships = set(canonical_by_membership)
        canonical_messages = {row["message_id"] for row in canonical}

        processed = [
            dict(row)
            for row in conn.execute(
                """SELECT membership_id,message_id,conversation_id,sequence_number,
                          sender_run_id,session_id,thread_id,attachment_count,has_attachment
                   FROM processed_message WHERE processing_run_id=?""",
                (run_id,),
            )
        ]
        processed_memberships = {int(row["membership_id"]) for row in processed}
        checks.update(
            {
                "canonical_membership_count": len(canonical_memberships),
                "canonical_message_count": len(canonical_messages),
                "processed_membership_count": len(processed),
                "run_input_membership_count": int(run["input_membership_count"]),
                "run_output_membership_count": int(run["output_membership_count"]),
                "run_canonical_message_count": int(run["canonical_message_count"]),
            }
        )

        if canonical_memberships != processed_memberships:
            _issue(
                issues,
                "ERROR",
                "A3_MEMBERSHIP_RECONCILIATION_FAILED",
                f"missing={len(canonical_memberships - processed_memberships)}, "
                f"extra={len(processed_memberships - canonical_memberships)}",
            )
        if int(run["input_membership_count"]) != len(canonical_memberships):
            _issue(
                issues,
                "ERROR",
                "A3_INPUT_COUNT_MISMATCH",
                "processing_run input_membership_count differs from current canonical memberships",
            )
        if int(run["output_membership_count"]) != len(processed):
            _issue(
                issues,
                "ERROR",
                "A3_OUTPUT_COUNT_MISMATCH",
                "processing_run output_membership_count differs from processed_message rows",
            )
        if int(run["canonical_message_count"]) != len(canonical_messages):
            _issue(
                issues,
                "ERROR",
                "A3_CANONICAL_MESSAGE_COUNT_MISMATCH",
                "processing_run canonical_message_count differs from canonical messages represented",
            )

        identity_mismatches = 0
        for row in processed:
            expected = canonical_by_membership.get(int(row["membership_id"]))
            if expected is None:
                continue
            if (
                int(row["message_id"]) != expected["message_id"]
                or int(row["conversation_id"]) != expected["conversation_id"]
            ):
                identity_mismatches += 1
        checks["processed_identity_mismatches"] = identity_mismatches
        if identity_mismatches:
            _issue(
                issues,
                "ERROR",
                "A3_CANONICAL_IDENTITY_MISMATCH",
                f"{identity_mismatches} processed membership(s) changed canonical identity",
            )

        processed_by_conversation: dict[int, list[dict[str, Any]]] = defaultdict(list)
        sequence_by_membership: dict[int, int] = {}
        for row in processed:
            processed_by_conversation[int(row["conversation_id"])].append(row)
            sequence_by_membership[int(row["membership_id"])] = int(row["sequence_number"])

        order_mismatches = 0
        for conversation_id, expected_rows in grouped.items():
            actual_rows = sorted(
                processed_by_conversation.get(conversation_id, []),
                key=lambda row: int(row["sequence_number"]),
            )
            expected_ids = [row["membership_id"] for row in expected_rows]
            actual_ids = [int(row["membership_id"]) for row in actual_rows]
            actual_sequences = [int(row["sequence_number"]) for row in actual_rows]
            if (
                expected_ids != actual_ids
                or actual_sequences != list(range(1, len(actual_rows) + 1))
            ):
                order_mismatches += 1
        checks["conversation_order_mismatches"] = order_mismatches
        if order_mismatches:
            _issue(
                issues,
                "ERROR",
                "A3_SEQUENCE_ORDER_MISMATCH",
                f"{order_mismatches} conversation(s) differ from deterministic canonical order",
            )

        session_members: dict[int, list[int]] = defaultdict(list)
        sender_members: dict[int, list[int]] = defaultdict(list)
        for row in processed:
            membership_id = int(row["membership_id"])
            session_members[int(row["session_id"])].append(membership_id)
            sender_members[int(row["sender_run_id"])].append(membership_id)

        expected_session_groups = (
            {frozenset(group) for group in _expected_sessions(grouped, gap_threshold_us)}
            if gap_threshold_us
            else set()
        )
        actual_session_groups = {frozenset(values) for values in session_members.values()}
        checks["expected_session_count"] = len(expected_session_groups)
        checks["actual_session_count"] = len(actual_session_groups)
        if gap_threshold_us and expected_session_groups != actual_session_groups:
            _issue(
                issues,
                "ERROR",
                "A3_SESSION_PARTITION_MISMATCH",
                "session membership partition differs from independently derived timestamp boundaries",
            )

        session_metadata_errors = 0
        for session_id, memberships in session_members.items():
            metadata = conn.execute(
                """SELECT conversation_id,first_membership_id,last_membership_id,
                          message_count,gap_threshold_us
                   FROM conversation_session
                   WHERE processing_run_id=? AND id=?""",
                (run_id, session_id),
            ).fetchone()
            ordered = sorted(memberships, key=lambda mid: sequence_by_membership.get(mid, 2**63 - 1))
            conversations = {
                canonical_by_membership[mid]["conversation_id"]
                for mid in memberships
                if mid in canonical_by_membership
            }
            if (
                metadata is None
                or not ordered
                or len(conversations) != 1
                or int(metadata["conversation_id"]) not in conversations
                or int(metadata["first_membership_id"]) != ordered[0]
                or int(metadata["last_membership_id"]) != ordered[-1]
                or int(metadata["message_count"]) != len(memberships)
                or int(metadata["gap_threshold_us"]) != gap_threshold_us
            ):
                session_metadata_errors += 1
        checks["session_metadata_errors"] = session_metadata_errors
        if session_metadata_errors:
            _issue(
                issues,
                "ERROR",
                "A3_SESSION_METADATA_MISMATCH",
                f"{session_metadata_errors} persisted session row(s) disagree with assignments",
            )

        expected_sender_groups = {frozenset(group) for group in _expected_sender_runs(grouped)}
        actual_sender_groups = {frozenset(values) for values in sender_members.values()}
        checks["expected_sender_run_count"] = len(expected_sender_groups)
        checks["actual_sender_run_count"] = len(actual_sender_groups)
        if expected_sender_groups != actual_sender_groups:
            _issue(
                issues,
                "ERROR",
                "A3_SENDER_RUN_PARTITION_MISMATCH",
                "sender-run partition differs from canonical consecutive-sender grouping",
            )

        sender_metadata_errors = 0
        for run_key, memberships in sender_members.items():
            metadata = conn.execute(
                """SELECT conversation_id,sender_id,first_membership_id,last_membership_id,
                          message_count
                   FROM sender_run WHERE processing_run_id=? AND id=?""",
                (run_id, run_key),
            ).fetchone()
            ordered = sorted(memberships, key=lambda mid: sequence_by_membership.get(mid, 2**63 - 1))
            canonical_rows = [canonical_by_membership[mid] for mid in memberships if mid in canonical_by_membership]
            conversations = {row["conversation_id"] for row in canonical_rows}
            senders = {row["sender_id"] for row in canonical_rows}
            persisted_sender = None if metadata is None or metadata["sender_id"] is None else int(metadata["sender_id"])
            if (
                metadata is None
                or not ordered
                or len(conversations) != 1
                or len(senders) != 1
                or int(metadata["conversation_id"]) not in conversations
                or persisted_sender not in senders
                or int(metadata["first_membership_id"]) != ordered[0]
                or int(metadata["last_membership_id"]) != ordered[-1]
                or int(metadata["message_count"]) != len(memberships)
            ):
                sender_metadata_errors += 1
        checks["sender_run_metadata_errors"] = sender_metadata_errors
        if sender_metadata_errors:
            _issue(
                issues,
                "ERROR",
                "A3_SENDER_RUN_METADATA_MISMATCH",
                f"{sender_metadata_errors} sender-run row(s) disagree with canonical memberships",
            )

        cross_conversation_threads = int(
            conn.execute(
                """SELECT COUNT(*) FROM conversation_thread_message ctm
                   JOIN conversation_thread ct
                     ON ct.processing_run_id=ctm.processing_run_id AND ct.id=ctm.thread_id
                   JOIN message_conversation mc ON mc.id=ctm.membership_id
                   WHERE ctm.processing_run_id=?
                     AND mc.conversation_id<>ct.conversation_id""",
                (run_id,),
            ).fetchone()[0]
        )
        checks["cross_conversation_thread_memberships"] = cross_conversation_threads
        if cross_conversation_threads:
            _issue(
                issues,
                "ERROR",
                "A3_THREAD_CROSSES_CONVERSATION",
                f"{cross_conversation_threads} thread membership(s) cross canonical conversations",
            )

        source_trace_missing = int(
            conn.execute(
                """SELECT COUNT(*) FROM processed_message pm
                   WHERE pm.processing_run_id=? AND NOT EXISTS (
                       SELECT 1 FROM message_source ms
                       WHERE ms.message_id=pm.message_id
                         AND ms.source_record_key IS NOT NULL
                         AND trim(ms.source_record_key)<>''
                   )""",
                (run_id,),
            ).fetchone()[0]
        )
        checks["processed_memberships_without_source_record_key"] = source_trace_missing
        if source_trace_missing:
            _issue(
                issues,
                "ERROR",
                "A3_SOURCE_TRACE_MISSING",
                f"{source_trace_missing} processed membership(s) cannot resolve to an A1 source_record_key",
            )

        attachment_feature_errors = int(
            conn.execute(
                """SELECT COUNT(*) FROM processed_message pm
                   WHERE pm.processing_run_id=? AND (
                       pm.attachment_count <> (
                           SELECT COUNT(*) FROM analysis_attachments aa
                           WHERE aa.message_id=pm.message_id
                       )
                       OR pm.has_attachment <> CASE WHEN EXISTS (
                           SELECT 1 FROM analysis_attachments aa
                           WHERE aa.message_id=pm.message_id
                       ) THEN 1 ELSE 0 END
                   )""",
                (run_id,),
            ).fetchone()[0]
        )
        checks["attachment_feature_errors"] = attachment_feature_errors
        if attachment_feature_errors:
            _issue(
                issues,
                "ERROR",
                "A3_ATTACHMENT_FEATURE_MISMATCH",
                f"{attachment_feature_errors} processed membership(s) disagree with A2 attachments",
            )

    except sqlite3.Error as exc:
        _issue(issues, "ERROR", "A3_VALIDATION_QUERY_FAILED", str(exc))
    finally:
        conn.close()

    return _finalize(path, issues, checks)


def _finalize(path: Path, issues: list[dict[str, Any]], checks: dict[str, Any]) -> dict[str, Any]:
    errors = sum(item["severity"] == "ERROR" for item in issues)
    warnings = sum(item["severity"] == "WARNING" for item in issues)
    return {
        "schema_version": 2,
        "status": STATUS_FAIL if errors else STATUS_WARNING if warnings else STATUS_PASS,
        "database": str(path),
        "checks": checks,
        "counts": {"errors": errors, "warnings": warnings},
        "issues": issues,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only A7 validator for integrated A3 processing"
    )
    parser.add_argument("database", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    report = validate_a3_database(args.database)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    return 1 if report["status"] == STATUS_FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
