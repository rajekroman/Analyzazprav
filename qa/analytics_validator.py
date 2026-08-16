from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from .a4_oracle import STATUS_FAIL, STATUS_PASS, validate_a4_against_oracle


STATUS_WARNING = "WARNING"

TRACEABLE_COLLECTIONS = (
    "conflicts",
    "silence_events",
    "time_buckets",
    "daily_metrics",
    "change_points",
    "period_metrics",
    "engagement_signals",
    "dyadic_regimes",
    "trend_summaries",
)


def _issue(issues: list[dict[str, Any]], severity: str, code: str, detail: str) -> None:
    issues.append({"severity": severity, "code": code, "detail": detail})


def _rows(value: Any, name: str, issues: list[dict[str, Any]]) -> list[Mapping[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        _issue(issues, "ERROR", "A4_RESULT_SHAPE_INVALID", f"{name} must be a list")
        return []
    result: list[Mapping[str, Any]] = []
    for index, row in enumerate(value):
        if not isinstance(row, Mapping):
            _issue(issues, "ERROR", "A4_RESULT_SHAPE_INVALID", f"{name}[{index}] must be an object")
            continue
        result.append(row)
    return result


def validate_analytics_result(
    source_messages: Iterable[Mapping[str, Any]],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the current A4/A3 analytics contract independently.

    The arithmetic/accounting oracle is implemented in ``qa.a4_oracle`` and does
    not import A4. This layer adds evidence-chain checks for all A4 derived rows
    that publish ``source_message_ids``.
    """

    source = [dict(row) for row in source_messages]
    issues: list[dict[str, Any]] = []
    checks: dict[str, Any] = {}

    ids = [int(row["message_id"]) for row in source if row.get("message_id") is not None]
    duplicate_ids = [mid for mid, count in Counter(ids).items() if count > 1]
    if duplicate_ids:
        _issue(
            issues,
            "ERROR",
            "SOURCE_MESSAGE_ID_DUPLICATE",
            f"Source contains duplicated message_id values: {duplicate_ids[:5]}",
        )

    try:
        oracle = validate_a4_against_oracle(source, result)
    except (TypeError, ValueError, KeyError) as exc:
        _issue(issues, "ERROR", "A4_ORACLE_INPUT_INVALID", str(exc))
        oracle = {"status": STATUS_FAIL, "issues": [], "expected": {}}

    issues.extend(dict(item) for item in oracle.get("issues", []))
    checks["oracle_status"] = oracle.get("status")
    expected = oracle.get("expected") if isinstance(oracle.get("expected"), Mapping) else {}
    checks["expected_source_message_count"] = expected.get("source_message_count")
    checks["expected_turn_count"] = expected.get("turn_count")
    checks["expected_session_count"] = expected.get("session_count")

    source_id_set = set(ids)
    session_by_message = {
        int(row["message_id"]): int(row["session_id"])
        for row in source
        if row.get("message_id") is not None and row.get("session_id") is not None
    }

    trace_rows_checked = 0
    trace_refs_checked = 0
    for collection in TRACEABLE_COLLECTIONS:
        for index, row in enumerate(_rows(result.get(collection), collection, issues)):
            if "source_message_ids" not in row:
                _issue(
                    issues,
                    "ERROR",
                    "A4_EVIDENCE_FIELD_MISSING",
                    f"{collection}[{index}] has no source_message_ids",
                )
                continue
            raw_ids = row.get("source_message_ids")
            if not isinstance(raw_ids, (list, tuple)):
                _issue(
                    issues,
                    "ERROR",
                    "A4_EVIDENCE_FIELD_INVALID",
                    f"{collection}[{index}].source_message_ids must be a list",
                )
                continue
            evidence: list[int] = []
            invalid_value = False
            for value in raw_ids:
                try:
                    evidence.append(int(value))
                except (TypeError, ValueError):
                    invalid_value = True
            if invalid_value:
                _issue(
                    issues,
                    "ERROR",
                    "A4_EVIDENCE_ID_INVALID",
                    f"{collection}[{index}] contains a non-integer message ID",
                )
            unknown = sorted(set(evidence) - source_id_set)
            if unknown:
                _issue(
                    issues,
                    "ERROR",
                    "A4_EVIDENCE_NOT_IN_SOURCE",
                    f"{collection}[{index}] references unknown message IDs: {unknown[:5]}",
                )
            trace_rows_checked += 1
            trace_refs_checked += len(evidence)

            if collection == "conflicts" and row.get("session_id") is not None:
                session_id = int(row["session_id"])
                outside = sorted(
                    mid for mid in evidence if mid in session_by_message and session_by_message[mid] != session_id
                )
                if outside:
                    _issue(
                        issues,
                        "ERROR",
                        "A4_CONFLICT_EVIDENCE_OUTSIDE_SESSION",
                        f"conflicts[{index}] contains evidence outside session {session_id}: {outside[:5]}",
                    )

    checks["trace_rows_checked"] = trace_rows_checked
    checks["trace_message_references_checked"] = trace_refs_checked

    diagnostics = result.get("diagnostics")
    if isinstance(diagnostics, Mapping):
        duplicates = diagnostics.get("duplicate_message_ids")
        if isinstance(duplicates, (list, tuple)) and duplicates:
            _issue(
                issues,
                "ERROR",
                "A4_DIAGNOSTIC_DUPLICATE_MESSAGE_IDS",
                f"A4 reports duplicated input message IDs: {list(duplicates)[:5]}",
            )
        checks["a4_self_reported_message_accounting_ok"] = diagnostics.get("message_accounting_ok")
        checks["uses_a3_session_boundaries"] = diagnostics.get("uses_a3_session_boundaries")

    error_count = sum(1 for item in issues if item.get("severity") == "ERROR")
    warning_count = sum(1 for item in issues if item.get("severity") == "WARNING")
    status = STATUS_FAIL if error_count else STATUS_WARNING if warning_count else STATUS_PASS
    return {
        "schema_version": 2,
        "status": status,
        "checks": checks,
        "counts": {"errors": error_count, "warnings": warning_count},
        "issues": issues,
        "oracle": expected,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate serialized A4 analytics against A7 oracle.")
    parser.add_argument("source_messages_json", type=Path)
    parser.add_argument("analytics_json", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    source = json.loads(args.source_messages_json.read_text(encoding="utf-8"))
    result = json.loads(args.analytics_json.read_text(encoding="utf-8"))
    if not isinstance(source, list) or not isinstance(result, Mapping):
        raise SystemExit("source must be a JSON array and analytics must be a JSON object")
    report = validate_analytics_result(source, result)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    return 1 if report["status"] == STATUS_FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
