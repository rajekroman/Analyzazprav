from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


STATUS_PASS = "PASS"
STATUS_WARNING = "WARNING"
STATUS_FAIL = "FAIL"


def _issue(issues: list[dict[str, Any]], severity: str, code: str, detail: str) -> None:
    issues.append({"severity": severity, "code": code, "detail": detail})


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    raise TypeError(f"expected mapping, got {type(value).__name__}")


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    raise TypeError(f"expected list/tuple, got {type(value).__name__}")


def _parse_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise TypeError(f"unsupported timestamp type {type(value).__name__}")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def validate_analytics_result(
    source_messages: Iterable[Mapping[str, Any]],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate A4 analytical accounting independently of A4 implementation.

    `result` is expected to be a JSON/asdict-compatible ConversationAnalytics
    representation. The validator does not call A4 formulas; it verifies that
    the analytical structure accounts for the supplied source messages exactly
    and that all evidence references resolve to those messages.
    """

    issues: list[dict[str, Any]] = []
    checks: dict[str, Any] = {}

    source = [dict(message) for message in source_messages]
    source_ids = [str(message.get("message_id")) for message in source]
    duplicate_source_ids = [key for key, count in Counter(source_ids).items() if count > 1]
    if duplicate_source_ids:
        _issue(
            issues,
            "ERROR",
            "SOURCE_MESSAGE_ID_DUPLICATE",
            f"Source input contains {len(duplicate_source_ids)} duplicated message_id value(s)",
        )

    reaction_ids = {
        str(message.get("message_id"))
        for message in source
        if bool(message.get("is_reaction", False))
    }
    analytic_ids = [
        str(message.get("message_id"))
        for message in source
        if not bool(message.get("is_reaction", False))
    ]
    analytic_counter = Counter(analytic_ids)

    try:
        turns = [_as_mapping(value) for value in _as_list(result.get("turns"))]
        sessions = [_as_mapping(value) for value in _as_list(result.get("sessions"))]
        latencies = [_as_mapping(value) for value in _as_list(result.get("latency_samples"))]
        conflicts = [_as_mapping(value) for value in _as_list(result.get("conflicts"))]
        participant_metrics = _as_mapping(result.get("participant_metrics", {}))
        diagnostics = _as_mapping(result.get("diagnostics", {}))
    except TypeError as exc:
        _issue(issues, "ERROR", "ANALYTICS_RESULT_SHAPE_INVALID", str(exc))
        return _finalize(issues, checks)

    checks.update(
        {
            "source_message_count": len(source),
            "analytic_source_message_count": len(analytic_ids),
            "reaction_source_message_count": len(reaction_ids),
            "result_message_count": result.get("message_count"),
            "result_turn_count": result.get("turn_count"),
            "result_session_count": result.get("session_count"),
        }
    )

    if result.get("message_count") != len(analytic_ids):
        _issue(
            issues,
            "ERROR",
            "ANALYTIC_MESSAGE_COUNT_MISMATCH",
            f"result.message_count={result.get('message_count')!r}, expected {len(analytic_ids)}",
        )
    if result.get("turn_count") != len(turns):
        _issue(
            issues,
            "ERROR",
            "TURN_COUNT_MISMATCH",
            f"result.turn_count={result.get('turn_count')!r}, actual {len(turns)}",
        )
    if result.get("session_count") != len(sessions):
        _issue(
            issues,
            "ERROR",
            "SESSION_COUNT_MISMATCH",
            f"result.session_count={result.get('session_count')!r}, actual {len(sessions)}",
        )

    turn_ids: list[str] = []
    turn_message_counter: Counter[str] = Counter()
    turn_by_id: dict[str, Mapping[str, Any]] = {}
    for index, turn in enumerate(turns):
        turn_id = str(turn.get("turn_id"))
        turn_ids.append(turn_id)
        turn_by_id[turn_id] = turn
        message_ids = [str(value) for value in _as_list(turn.get("message_ids"))]
        turn_message_counter.update(message_ids)
        if turn.get("message_count") != len(message_ids):
            _issue(
                issues,
                "ERROR",
                "TURN_MESSAGE_COUNT_MISMATCH",
                f"Turn {turn_id!r} declares {turn.get('message_count')!r} messages but references {len(message_ids)}",
            )
        reaction_hits = sorted(set(message_ids) & reaction_ids)
        if reaction_hits:
            _issue(
                issues,
                "ERROR",
                "REACTION_INCLUDED_IN_ANALYTIC_TURN",
                f"Turn {turn_id!r} includes excluded reaction message(s): {reaction_hits[:5]}",
            )

    duplicate_turn_ids = [key for key, count in Counter(turn_ids).items() if count > 1]
    if duplicate_turn_ids:
        _issue(
            issues,
            "ERROR",
            "TURN_ID_DUPLICATE",
            f"{len(duplicate_turn_ids)} duplicated turn_id value(s)",
        )

    missing_from_turns = sorted((analytic_counter - turn_message_counter).elements())
    extra_in_turns = sorted((turn_message_counter - analytic_counter).elements())
    checks["analytic_messages_missing_from_turns"] = len(missing_from_turns)
    checks["turn_message_references_not_in_source"] = len(extra_in_turns)
    if missing_from_turns:
        _issue(
            issues,
            "ERROR",
            "ANALYTIC_MESSAGES_MISSING_FROM_TURNS",
            f"{len(missing_from_turns)} source message occurrence(s) are missing from turns; examples: {missing_from_turns[:5]}",
        )
    if extra_in_turns:
        _issue(
            issues,
            "ERROR",
            "TURN_MESSAGES_NOT_IN_SOURCE",
            f"{len(extra_in_turns)} turn message reference(s) are not valid analytic source messages; examples: {extra_in_turns[:5]}",
        )

    session_ids: list[str] = []
    session_turn_counter: Counter[str] = Counter()
    session_by_id: dict[str, Mapping[str, Any]] = {}
    expected_latency_pairs: set[tuple[str, str, str]] = set()
    session_message_ids: dict[str, set[str]] = {}

    for session in sessions:
        session_id = str(session.get("session_id"))
        session_ids.append(session_id)
        session_by_id[session_id] = session
        session_turn_ids = [str(value) for value in _as_list(session.get("turn_ids"))]
        session_turn_counter.update(session_turn_ids)
        resolved_turns = [turn_by_id.get(turn_id) for turn_id in session_turn_ids]
        missing_turn_refs = [turn_id for turn_id, turn in zip(session_turn_ids, resolved_turns) if turn is None]
        if missing_turn_refs:
            _issue(
                issues,
                "ERROR",
                "SESSION_TURN_NOT_FOUND",
                f"Session {session_id!r} references missing turn(s): {missing_turn_refs[:5]}",
            )
            continue

        concrete_turns = [turn for turn in resolved_turns if turn is not None]
        if concrete_turns:
            expected_initiator = str(concrete_turns[0].get("participant_id"))
            if str(session.get("initiator_id")) != expected_initiator:
                _issue(
                    issues,
                    "ERROR",
                    "SESSION_INITIATOR_MISMATCH",
                    f"Session {session_id!r} initiator {session.get('initiator_id')!r} != first turn participant {expected_initiator!r}",
                )

        messages_for_session: set[str] = set()
        for turn in concrete_turns:
            messages_for_session.update(str(value) for value in _as_list(turn.get("message_ids")))
        session_message_ids[session_id] = messages_for_session

        for previous, current in zip(concrete_turns, concrete_turns[1:]):
            if previous.get("participant_id") != current.get("participant_id"):
                expected_latency_pairs.add(
                    (session_id, str(previous.get("turn_id")), str(current.get("turn_id")))
                )

    duplicate_session_ids = [key for key, count in Counter(session_ids).items() if count > 1]
    if duplicate_session_ids:
        _issue(
            issues,
            "ERROR",
            "SESSION_ID_DUPLICATE",
            f"{len(duplicate_session_ids)} duplicated session_id value(s)",
        )

    turn_counter = Counter(turn_ids)
    missing_turns_from_sessions = sorted((turn_counter - session_turn_counter).elements())
    extra_session_turns = sorted((session_turn_counter - turn_counter).elements())
    checks["turns_missing_from_sessions"] = len(missing_turns_from_sessions)
    checks["session_turn_references_invalid_or_duplicated"] = len(extra_session_turns)
    if missing_turns_from_sessions:
        _issue(
            issues,
            "ERROR",
            "TURNS_MISSING_FROM_SESSIONS",
            f"{len(missing_turns_from_sessions)} turn(s) are not assigned to a session",
        )
    if extra_session_turns:
        _issue(
            issues,
            "ERROR",
            "SESSION_TURN_ACCOUNTING_INVALID",
            f"{len(extra_session_turns)} session turn reference occurrence(s) are invalid or duplicated",
        )

    actual_latency_pairs: set[tuple[str, str, str]] = set()
    for sample in latencies:
        session_id = str(sample.get("session_id"))
        previous_id = str(sample.get("previous_turn_id"))
        response_id = str(sample.get("response_turn_id"))
        pair = (session_id, previous_id, response_id)
        actual_latency_pairs.add(pair)
        previous = turn_by_id.get(previous_id)
        response = turn_by_id.get(response_id)
        if session_id not in session_by_id or previous is None or response is None:
            _issue(
                issues,
                "ERROR",
                "LATENCY_EVIDENCE_NOT_FOUND",
                f"Latency sample references unknown session/turn: {pair}",
            )
            continue
        if previous.get("participant_id") == response.get("participant_id"):
            _issue(
                issues,
                "ERROR",
                "LATENCY_SAME_PARTICIPANT",
                f"Latency sample {pair} is not a cross-participant response",
            )
        latency = sample.get("latency_seconds")
        if not isinstance(latency, (int, float)) or isinstance(latency, bool) or latency < 0:
            _issue(issues, "ERROR", "LATENCY_INVALID", f"Latency sample {pair} has invalid value {latency!r}")
            continue
        try:
            expected = (_parse_time(response.get("start_at")) - _parse_time(previous.get("end_at"))).total_seconds()
        except (TypeError, ValueError) as exc:
            _issue(issues, "ERROR", "LATENCY_TIMESTAMP_INVALID", f"{pair}: {exc}")
            continue
        if abs(float(latency) - expected) > 1e-6:
            _issue(
                issues,
                "ERROR",
                "LATENCY_VALUE_MISMATCH",
                f"Latency sample {pair}={latency}, expected {expected}",
            )

    missing_latency_pairs = sorted(expected_latency_pairs - actual_latency_pairs)
    extra_latency_pairs = sorted(actual_latency_pairs - expected_latency_pairs)
    checks["expected_latency_samples"] = len(expected_latency_pairs)
    checks["actual_latency_samples"] = len(actual_latency_pairs)
    if missing_latency_pairs:
        _issue(
            issues,
            "ERROR",
            "LATENCY_SAMPLES_MISSING",
            f"{len(missing_latency_pairs)} cross-participant turn transition(s) have no latency sample",
        )
    if extra_latency_pairs:
        _issue(
            issues,
            "ERROR",
            "LATENCY_SAMPLES_EXTRA",
            f"{len(extra_latency_pairs)} latency sample(s) do not correspond to a valid transition",
        )

    valid_analytic_id_set = set(analytic_ids)
    for conflict in conflicts:
        session_id = str(conflict.get("session_id"))
        evidence = {str(value) for value in _as_list(conflict.get("source_message_ids"))}
        unknown = sorted(evidence - valid_analytic_id_set)
        if unknown:
            _issue(
                issues,
                "ERROR",
                "CONFLICT_EVIDENCE_NOT_IN_SOURCE",
                f"Conflict {session_id!r} references unknown message(s): {unknown[:5]}",
            )
        if session_id not in session_by_id:
            _issue(
                issues,
                "ERROR",
                "CONFLICT_SESSION_NOT_FOUND",
                f"Conflict references unknown session {session_id!r}",
            )
        else:
            outside = sorted(evidence - session_message_ids.get(session_id, set()))
            if outside:
                _issue(
                    issues,
                    "ERROR",
                    "CONFLICT_EVIDENCE_OUTSIDE_SESSION",
                    f"Conflict {session_id!r} references message(s) outside its session: {outside[:5]}",
                )

    metric_message_sum = 0
    metric_turn_sum = 0
    metric_initiation_sum = 0
    initiation_share_sum = 0.0
    for participant, raw_metrics in participant_metrics.items():
        try:
            metrics = _as_mapping(raw_metrics)
            metric_message_sum += int(metrics.get("message_count", 0) or 0)
            metric_turn_sum += int(metrics.get("turn_count", 0) or 0)
            metric_initiation_sum += int(metrics.get("initiations", 0) or 0)
            initiation_share_sum += float(metrics.get("initiation_share", 0.0) or 0.0)
        except (TypeError, ValueError):
            _issue(
                issues,
                "ERROR",
                "PARTICIPANT_METRICS_INVALID",
                f"Metrics for participant {participant!r} contain non-numeric accounting values",
            )

    checks["participant_message_count_sum"] = metric_message_sum
    checks["participant_turn_count_sum"] = metric_turn_sum
    checks["participant_initiation_sum"] = metric_initiation_sum
    if metric_message_sum != len(analytic_ids):
        _issue(
            issues,
            "ERROR",
            "PARTICIPANT_MESSAGE_ACCOUNTING_MISMATCH",
            f"Participant message counts sum to {metric_message_sum}; expected {len(analytic_ids)}",
        )
    if metric_turn_sum != len(turns):
        _issue(
            issues,
            "ERROR",
            "PARTICIPANT_TURN_ACCOUNTING_MISMATCH",
            f"Participant turn counts sum to {metric_turn_sum}; expected {len(turns)}",
        )
    if metric_initiation_sum != len(sessions):
        _issue(
            issues,
            "ERROR",
            "PARTICIPANT_INITIATION_ACCOUNTING_MISMATCH",
            f"Participant initiations sum to {metric_initiation_sum}; expected {len(sessions)}",
        )
    if sessions and abs(initiation_share_sum - 1.0) > 1e-9:
        _issue(
            issues,
            "ERROR",
            "INITIATION_SHARE_SUM_MISMATCH",
            f"Participant initiation shares sum to {initiation_share_sum}; expected 1.0",
        )

    if diagnostics.get("source_message_count") != len(source):
        _issue(
            issues,
            "ERROR",
            "DIAGNOSTIC_SOURCE_COUNT_MISMATCH",
            f"diagnostics.source_message_count={diagnostics.get('source_message_count')!r}, expected {len(source)}",
        )
    if diagnostics.get("excluded_reactions") != len(reaction_ids):
        _issue(
            issues,
            "ERROR",
            "DIAGNOSTIC_REACTION_COUNT_MISMATCH",
            f"diagnostics.excluded_reactions={diagnostics.get('excluded_reactions')!r}, expected {len(reaction_ids)}",
        )

    return _finalize(issues, checks)


def _finalize(issues: list[dict[str, Any]], checks: dict[str, Any]) -> dict[str, Any]:
    errors = sum(1 for issue in issues if issue["severity"] == "ERROR")
    warnings = sum(1 for issue in issues if issue["severity"] == "WARNING")
    status = STATUS_FAIL if errors else STATUS_WARNING if warnings else STATUS_PASS
    return {
        "schema_version": 1,
        "status": status,
        "checks": checks,
        "counts": {"errors": errors, "warnings": warnings},
        "issues": issues,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a serialized A4 ConversationAnalytics result.")
    parser.add_argument("source_messages", type=Path, help="JSON file containing source message objects")
    parser.add_argument("analytics_result", type=Path, help="JSON file containing serialized A4 result")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    source = json.loads(args.source_messages.read_text(encoding="utf-8"))
    result = json.loads(args.analytics_result.read_text(encoding="utf-8"))
    report = validate_analytics_result(source, result)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    return 1 if report["status"] == STATUS_FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
