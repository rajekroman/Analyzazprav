from __future__ import annotations

import ast
from collections import Counter, defaultdict
from math import floor, isclose
from statistics import mean, median
from typing import Any, Iterable, Mapping, Sequence

from .staging import STATUS_FAIL, STATUS_PASS, STATUS_WARNING

VERDICT_VALID = "VALID"
VERDICT_INVALID = "INVALID"
VERDICT_NEEDS_REVIEW = "NEEDS_REVIEW"


def _issue(issues: list[dict[str, Any]], severity: str, code: str, detail: str) -> None:
    issues.append({"severity": severity, "code": code, "detail": detail})


def _finalize(module: str, issues: list[dict[str, Any]], checks: Mapping[str, Any]) -> dict[str, Any]:
    errors = sum(item.get("severity") == "ERROR" for item in issues)
    warnings = sum(item.get("severity") == "WARNING" for item in issues)
    if errors:
        status = STATUS_FAIL
        verdict = VERDICT_INVALID
    elif warnings:
        status = STATUS_WARNING
        verdict = VERDICT_NEEDS_REVIEW
    else:
        status = STATUS_PASS
        verdict = VERDICT_VALID
    return {
        "schema_version": 1,
        "module": module,
        "status": status,
        "verdict": verdict,
        "checks": dict(checks),
        "issues": issues,
    }


def _percentile(values: Sequence[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = floor(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _same_number(actual: Any, expected: float | int | None, *, tolerance: float = 1e-9) -> bool:
    if actual is None or expected is None:
        return actual is None and expected is None
    if isinstance(actual, bool):
        return False
    try:
        return isclose(float(actual), float(expected), rel_tol=tolerance, abs_tol=tolerance)
    except (TypeError, ValueError):
        return False


def _expected_turns(source: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(source, key=lambda row: (int(row["sequence_number"]), int(row["message_id"])))
    turns: list[dict[str, Any]] = []
    batch: list[Mapping[str, Any]] = []

    def flush() -> None:
        if not batch:
            return
        turns.append(
            {
                "turn_id": len(turns) + 1,
                "conversation_id": int(batch[0]["conversation_id"]),
                "session_id": int(batch[0]["session_id"]),
                "participant_id": batch[0].get("participant_id"),
                "start_us": batch[0].get("timestamp_us"),
                "end_us": batch[-1].get("timestamp_us"),
                "message_ids": [int(row["message_id"]) for row in batch],
                "message_count": len(batch),
                "word_count": sum(int(row.get("word_count") or 0) for row in batch),
            }
        )

    for row in ordered:
        if not batch:
            batch.append(row)
            continue
        if (
            int(row["session_id"]) == int(batch[-1]["session_id"])
            and row.get("participant_id") == batch[-1].get("participant_id")
        ):
            batch.append(row)
        else:
            flush()
            batch = [row]
    flush()
    return turns


def _expected_responses(turns: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for previous, current in zip(turns, turns[1:]):
        if int(previous["session_id"]) != int(current["session_id"]):
            continue
        left = previous.get("participant_id")
        right = current.get("participant_id")
        if left is None or right is None or left == right:
            continue
        latency = None
        if previous.get("end_us") is not None and current.get("start_us") is not None:
            delta = int(current["start_us"]) - int(previous["end_us"])
            if delta >= 0:
                latency = delta / 1_000_000
        rows.append(
            {
                "conversation_id": int(current["conversation_id"]),
                "session_id": int(current["session_id"]),
                "from_participant_id": int(left),
                "responder_id": int(right),
                "previous_turn_id": int(previous["turn_id"]),
                "response_turn_id": int(current["turn_id"]),
                "latency_seconds": latency,
                "response_effort_ratio": float(current["word_count"]) / max(1, int(previous["word_count"])),
            }
        )
    return rows


def _unanswered_counts(turns: Sequence[Mapping[str, Any]]) -> Counter[int]:
    grouped: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for turn in turns:
        grouped[int(turn["session_id"])].append(turn)
    counts: Counter[int] = Counter()
    for session_turns in grouped.values():
        for index, turn in enumerate(session_turns):
            participant = turn.get("participant_id")
            if participant is None:
                continue
            answered = any(
                later.get("participant_id") is not None
                and later.get("participant_id") != participant
                for later in session_turns[index + 1 :]
            )
            if not answered:
                counts[int(participant)] += 1
    return counts


def _participant_metrics_oracle(
    source: Sequence[Mapping[str, Any]],
    turns: Sequence[Mapping[str, Any]],
    responses: Sequence[Mapping[str, Any]],
) -> dict[int, dict[str, Any]]:
    participants = sorted({int(row["participant_id"]) for row in source if row.get("participant_id") is not None})
    message_count = Counter(int(row["participant_id"]) for row in source if row.get("participant_id") is not None)
    word_count: Counter[int] = Counter()
    for row in source:
        if row.get("participant_id") is not None:
            word_count[int(row["participant_id"])] += int(row.get("word_count") or 0)
    turn_count = Counter(int(row["participant_id"]) for row in turns if row.get("participant_id") is not None)

    initiations: Counter[int] = Counter()
    seen_sessions: set[int] = set()
    known_initiated_sessions = 0
    for turn in turns:
        session_id = int(turn["session_id"])
        if session_id in seen_sessions:
            continue
        seen_sessions.add(session_id)
        participant = turn.get("participant_id")
        if participant is not None:
            initiations[int(participant)] += 1
            known_initiated_sessions += 1

    unanswered = _unanswered_counts(turns)
    latency_by_responder: dict[int, list[float]] = defaultdict(list)
    effort_by_responder: dict[int, list[float]] = defaultdict(list)
    response_turn_count: Counter[int] = Counter()
    for sample in responses:
        responder = int(sample["responder_id"])
        response_turn_count[responder] += 1
        if sample.get("latency_seconds") is not None:
            latency_by_responder[responder].append(float(sample["latency_seconds"]))
        effort_by_responder[responder].append(float(sample["response_effort_ratio"]))

    result: dict[int, dict[str, Any]] = {}
    for participant in participants:
        latencies = latency_by_responder.get(participant, [])
        efforts = effort_by_responder.get(participant, [])
        result[participant] = {
            "message_count": message_count[participant],
            "word_count": word_count[participant],
            "turn_count": turn_count[participant],
            "initiations": initiations[participant],
            "initiation_share": initiations[participant] / max(1, known_initiated_sessions),
            "response_turn_count": response_turn_count[participant],
            "latency_sample_count": len(latencies),
            "unanswered_turn_count": unanswered[participant],
            "mean_response_latency_seconds": mean(latencies) if latencies else None,
            "median_response_latency_seconds": median(latencies) if latencies else None,
            "p25_response_latency_seconds": _percentile(latencies, 0.25),
            "p75_response_latency_seconds": _percentile(latencies, 0.75),
            "p90_response_latency_seconds": _percentile(latencies, 0.90),
            "median_response_effort_ratio": median(efforts) if efforts else None,
        }
    return result


def _reciprocity(metrics: Mapping[int, Mapping[str, Any]], name: str) -> float | None:
    if len(metrics) != 2:
        return None
    values = [float(row.get(name) or 0) for row in metrics.values()]
    if max(values, default=0.0) == 0.0:
        return 1.0
    return min(values) / max(values)


def validate_a4_result(source_messages: Sequence[Mapping[str, Any]], result: Mapping[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    checks: dict[str, Any] = {}
    source = list(source_messages)
    if not source:
        _issue(issues, "ERROR", "A4_SOURCE_EMPTY", "Independent A4 contract requires a non-empty source fixture")
        return _finalize("A4", issues, checks)

    conversation_ids = {int(row["conversation_id"]) for row in source}
    if len(conversation_ids) != 1:
        _issue(issues, "ERROR", "A4_SOURCE_MULTIPLE_CONVERSATIONS", "A4 oracle validates one conversation at a time")
        return _finalize("A4", issues, checks)

    message_ids = [int(row["message_id"]) for row in source]
    if len(message_ids) != len(set(message_ids)):
        _issue(issues, "ERROR", "A4_SOURCE_DUPLICATE_MESSAGE_ID", "Source fixture has duplicate message IDs within one conversation")
    memberships = [row.get("membership_id") for row in source if row.get("membership_id") is not None]
    if len(memberships) != len(set(memberships)):
        _issue(issues, "ERROR", "A4_SOURCE_DUPLICATE_MEMBERSHIP_ID", "Source fixture has duplicate membership IDs")

    expected_turns = _expected_turns(source)
    expected_responses = _expected_responses(expected_turns)
    expected_metrics = _participant_metrics_oracle(source, expected_turns, expected_responses)
    expected_sessions = len({int(row["session_id"]) for row in source})
    known = sum(row.get("participant_id") is not None for row in source)

    checks.update(
        {
            "source_message_count": len(source),
            "expected_turn_count": len(expected_turns),
            "expected_session_count": expected_sessions,
            "expected_response_sample_count": len(expected_responses),
        }
    )

    scalar_expectations = {
        "conversation_id": next(iter(conversation_ids)),
        "source_message_count": len(source),
        "known_sender_message_count": known,
        "unknown_sender_message_count": len(source) - known,
        "turn_count": len(expected_turns),
        "session_count": expected_sessions,
    }
    for name, expected in scalar_expectations.items():
        if result.get(name) != expected:
            _issue(issues, "ERROR", "A4_SCALAR_MISMATCH", f"{name}: expected {expected!r}, got {result.get(name)!r}")

    actual_turns = list(result.get("turns") or [])
    if len(actual_turns) != len(expected_turns):
        _issue(issues, "ERROR", "A4_TURN_COUNT_MISMATCH", f"Expected {len(expected_turns)} turn rows, got {len(actual_turns)}")
    for index, expected in enumerate(expected_turns):
        if index >= len(actual_turns):
            break
        actual = actual_turns[index]
        for name in ("turn_id", "conversation_id", "session_id", "participant_id", "start_us", "end_us", "message_count", "word_count"):
            if actual.get(name) != expected.get(name):
                _issue(issues, "ERROR", "A4_TURN_FIELD_MISMATCH", f"turn[{index}].{name}: expected {expected.get(name)!r}, got {actual.get(name)!r}")
        actual_ids = [int(value) for value in actual.get("message_ids") or []]
        if actual_ids != expected["message_ids"]:
            _issue(issues, "ERROR", "A4_TURN_MESSAGE_PARTITION_MISMATCH", f"turn[{index}] expected message IDs {expected['message_ids']}, got {actual_ids}")

    actual_responses = list(result.get("response_samples") or [])
    if len(actual_responses) != len(expected_responses):
        _issue(issues, "ERROR", "A4_RESPONSE_SAMPLE_COUNT_MISMATCH", f"Expected {len(expected_responses)} response samples, got {len(actual_responses)}")
    actual_by_pair = {
        (int(row.get("previous_turn_id")), int(row.get("response_turn_id"))): row
        for row in actual_responses
        if row.get("previous_turn_id") is not None and row.get("response_turn_id") is not None
    }
    for expected in expected_responses:
        key = (expected["previous_turn_id"], expected["response_turn_id"])
        actual = actual_by_pair.get(key)
        if actual is None:
            _issue(issues, "ERROR", "A4_RESPONSE_SAMPLE_MISSING", f"Missing response transition {key}")
            continue
        for name in ("conversation_id", "session_id", "from_participant_id", "responder_id"):
            if actual.get(name) != expected[name]:
                _issue(issues, "ERROR", "A4_RESPONSE_FIELD_MISMATCH", f"transition {key} {name}: expected {expected[name]!r}, got {actual.get(name)!r}")
        for name in ("latency_seconds", "response_effort_ratio"):
            if not _same_number(actual.get(name), expected[name]):
                _issue(issues, "ERROR", "A4_RESPONSE_VALUE_MISMATCH", f"transition {key} {name}: expected {expected[name]!r}, got {actual.get(name)!r}")

    raw_metrics = result.get("participant_metrics") or {}
    actual_metrics: dict[int, Mapping[str, Any]] = {}
    for key, value in raw_metrics.items():
        try:
            actual_metrics[int(key)] = value
        except (TypeError, ValueError):
            _issue(issues, "ERROR", "A4_PARTICIPANT_KEY_INVALID", f"Invalid participant key {key!r}")
    if set(actual_metrics) != set(expected_metrics):
        _issue(issues, "ERROR", "A4_PARTICIPANT_SET_MISMATCH", f"Expected participants {sorted(expected_metrics)}, got {sorted(actual_metrics)}")
    metric_names = (
        "message_count",
        "word_count",
        "turn_count",
        "initiations",
        "initiation_share",
        "response_turn_count",
        "latency_sample_count",
        "unanswered_turn_count",
        "mean_response_latency_seconds",
        "median_response_latency_seconds",
        "p25_response_latency_seconds",
        "p75_response_latency_seconds",
        "p90_response_latency_seconds",
        "median_response_effort_ratio",
    )
    for participant, expected in expected_metrics.items():
        actual = actual_metrics.get(participant)
        if actual is None:
            continue
        for name in metric_names:
            if not _same_number(actual.get(name), expected[name]):
                _issue(issues, "ERROR", "A4_PARTICIPANT_METRIC_MISMATCH", f"participant {participant} {name}: expected {expected[name]!r}, got {actual.get(name)!r}")

    reciprocity = result.get("reciprocity") or {}
    for output_name, metric_name in (
        ("message_reciprocity", "message_count"),
        ("word_reciprocity", "word_count"),
        ("turn_reciprocity", "turn_count"),
        ("initiation_reciprocity", "initiations"),
    ):
        expected = _reciprocity(expected_metrics, metric_name)
        if not _same_number(reciprocity.get(output_name), expected):
            _issue(issues, "ERROR", "A4_RECIPROCITY_MISMATCH", f"{output_name}: expected {expected!r}, got {reciprocity.get(output_name)!r}")

    source_id_set = set(message_ids)
    evidence_collections = (
        "conflicts",
        "silence_events",
        "time_buckets",
        "daily_metrics",
        "change_points",
        "period_metrics",
        "engagement_signals",
        "dyadic_regimes",
        "trend_summaries",
        "topic_candidates",
    )
    for collection_name in evidence_collections:
        for index, row in enumerate(result.get(collection_name) or []):
            ids = {int(value) for value in row.get("source_message_ids") or []}
            unknown_ids = sorted(ids - source_id_set)
            if unknown_ids:
                _issue(issues, "ERROR", "A4_EVIDENCE_OUTSIDE_SOURCE", f"{collection_name}[{index}] references unknown message IDs {unknown_ids}")

    candidate_keys = {str(row.get("topic_key")) for row in result.get("topic_candidates") or []}
    for index, row in enumerate(result.get("topic_evidence") or []):
        message_id = int(row.get("message_id"))
        if message_id not in source_id_set:
            _issue(issues, "ERROR", "A4_TOPIC_EVIDENCE_OUTSIDE_SOURCE", f"topic_evidence[{index}] references {message_id}")
        if str(row.get("topic_key")) not in candidate_keys:
            _issue(issues, "ERROR", "A4_TOPIC_EVIDENCE_WITHOUT_CANDIDATE", f"topic_evidence[{index}] has no emitted topic candidate")

    return _finalize("A4", issues, checks)


def _safe_excerpt(text: str) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= 240:
        return normalized
    return normalized[:239].rstrip() + "…"


def _context_message_map(context: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in context.get("messages") or []:
        message_id = str(row.get("id"))
        if message_id in result:
            raise ValueError(f"duplicate context message id: {message_id}")
        result[message_id] = row
    return result


def _validate_evidence_ref(
    evidence: Any,
    *,
    path: str,
    context_messages: Mapping[str, Mapping[str, Any]],
    metrics: Mapping[str, Mapping[str, Any]],
    issues: list[dict[str, Any]],
) -> None:
    if not isinstance(evidence, Mapping):
        _issue(issues, "ERROR", "A5_EVIDENCE_OBJECT_MISSING", f"{path} must be an evidence object")
        return
    ids = [str(value) for value in evidence.get("message_ids") or []]
    if not ids:
        _issue(issues, "ERROR", "A5_EVIDENCE_MESSAGE_IDS_EMPTY", f"{path}.message_ids must not be empty")
    if len(ids) != len(set(ids)):
        _issue(issues, "ERROR", "A5_EVIDENCE_MESSAGE_IDS_DUPLICATE", f"{path}.message_ids contains duplicates")
    unknown = [message_id for message_id in ids if message_id not in context_messages]
    if unknown:
        _issue(issues, "ERROR", "A5_EVIDENCE_OUTSIDE_CONTEXT", f"{path} references unknown context IDs {unknown}")

    snapshots = list(evidence.get("messages") or [])
    snapshot_by_id = {str(row.get("message_id")): row for row in snapshots if isinstance(row, Mapping)}
    for message_id in ids:
        source = context_messages.get(message_id)
        snapshot = snapshot_by_id.get(message_id)
        if source is None or snapshot is None:
            if source is not None:
                _issue(issues, "ERROR", "A5_EVIDENCE_SNAPSHOT_MISSING", f"{path} has no enriched snapshot for {message_id}")
            continue
        expected = {
            "message_id": message_id,
            "timestamp": str(source.get("timestamp")),
            "sender_id": str(source.get("participant_id")),
            "excerpt": _safe_excerpt(str(source.get("text") or "")),
        }
        for name, value in expected.items():
            if str(snapshot.get(name)) != value:
                _issue(issues, "ERROR", "A5_EVIDENCE_SNAPSHOT_MISMATCH", f"{path} {message_id} {name}: expected {value!r}, got {snapshot.get(name)!r}")

    for index, metric in enumerate(evidence.get("metrics") or []):
        if not isinstance(metric, Mapping):
            _issue(issues, "ERROR", "A5_METRIC_EVIDENCE_INVALID", f"{path}.metrics[{index}] is not an object")
            continue
        phase = str(metric.get("phase"))
        name = str(metric.get("name"))
        phase_metrics = metrics.get(phase) or {}
        if name not in phase_metrics:
            _issue(issues, "ERROR", "A5_METRIC_EVIDENCE_OUTSIDE_CONTEXT", f"{path}.metrics[{index}] references {phase}.{name}")
            continue
        if not _same_number(metric.get("value"), phase_metrics[name]):
            _issue(issues, "ERROR", "A5_METRIC_EVIDENCE_VALUE_MISMATCH", f"{path}.metrics[{index}] expected value {phase_metrics[name]!r}, got {metric.get('value')!r}")


def validate_a5_result(context: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    checks: dict[str, Any] = {}
    try:
        context_messages = _context_message_map(context)
    except ValueError as exc:
        _issue(issues, "ERROR", "A5_CONTEXT_DUPLICATE_MESSAGE_ID", str(exc))
        return _finalize("A5", issues, checks)
    metrics = context.get("metrics") or {}
    metrics_by_phase = {
        "before": metrics.get("before") or {},
        "during": metrics.get("during") or {},
        "after": metrics.get("after") or {},
    }
    checks["context_message_count"] = len(context_messages)

    _validate_evidence_ref(
        result.get("summary_evidence"),
        path="summary_evidence",
        context_messages=context_messages,
        metrics=metrics_by_phase,
        issues=issues,
    )

    for index, row in enumerate(result.get("observations") or []):
        _validate_evidence_ref(
            row.get("evidence") if isinstance(row, Mapping) else None,
            path=f"observations[{index}].evidence",
            context_messages=context_messages,
            metrics=metrics_by_phase,
            issues=issues,
        )
    for collection_name in ("interpretations", "patterns"):
        for index, row in enumerate(result.get(collection_name) or []):
            evidence = row.get("evidence") if isinstance(row, Mapping) else None
            _validate_evidence_ref(
                evidence,
                path=f"{collection_name}[{index}].evidence",
                context_messages=context_messages,
                metrics=metrics_by_phase,
                issues=issues,
            )

    turning_points = list(result.get("turning_points") or [])
    turning_evidence = list(result.get("turning_point_evidence") or [])
    if len(turning_points) != len(turning_evidence):
        _issue(issues, "ERROR", "A5_TURNING_POINT_EVIDENCE_COUNT_MISMATCH", f"{len(turning_points)} turning points but {len(turning_evidence)} evidence refs")
    for index, evidence in enumerate(turning_evidence):
        _validate_evidence_ref(
            evidence,
            path=f"turning_point_evidence[{index}]",
            context_messages=context_messages,
            metrics=metrics_by_phase,
            issues=issues,
        )

    for field_name in ("participant_p1", "participant_p2", "shared_dynamic"):
        value = result.get(field_name)
        evidence = result.get(f"{field_name}_evidence")
        if value not in (None, "") and evidence is None:
            _issue(issues, "ERROR", "A5_ASSERTION_EVIDENCE_MISSING", f"{field_name} is populated without evidence")
        if evidence is not None:
            _validate_evidence_ref(
                evidence,
                path=f"{field_name}_evidence",
                context_messages=context_messages,
                metrics=metrics_by_phase,
                issues=issues,
            )

    checks["assertion_evidence_surface_checked"] = True
    return _finalize("A5", issues, checks)


def _rows_by_membership(rows: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        membership = str(row.get("membership_id"))
        if membership in result:
            raise ValueError(f"duplicate membership_id {membership}")
        result[membership] = row
    return result


def _canonical_rows(rows: Iterable[Mapping[str, Any]]) -> Counter[str]:
    return Counter(
        repr(sorted((str(key), value) for key, value in row.items()))
        for row in rows
    )


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _result_get_key(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    if node.func.attr != "get" or not isinstance(node.func.value, ast.Name) or node.func.value.id != "result":
        return None
    if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
        return None
    return node.args[0].value


def _function_calls(function: ast.FunctionDef, target: str) -> list[ast.Call]:
    return [node for node in ast.walk(function) if isinstance(node, ast.Call) and _call_name(node) == target]


def validate_a6_renderer_source(source: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        _issue(issues, "ERROR", "A6_RENDERER_SYNTAX_ERROR", str(exc))
        return issues
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    required = {"render_a5_execution", "render_assertion", "render_evidence_ref", "render_result_evidence"}
    missing = sorted(required - set(functions))
    if missing:
        _issue(issues, "ERROR", "A6_RENDERER_FUNCTION_MISSING", ", ".join(missing))
        return issues

    execution_calls = _function_calls(functions["render_a5_execution"], "render_assertion")
    direct_keys: set[str] = set()
    for call in execution_calls:
        for argument in call.args:
            key = _result_get_key(argument)
            if key:
                direct_keys.add(key)
    for key in ("summary", "participant_p1", "participant_p2", "shared_dynamic"):
        if key not in direct_keys:
            _issue(issues, "ERROR", "A6_ASSERTION_RENDER_NOT_EVIDENCE_AWARE", f"render_a5_execution does not route result.{key} through render_assertion")

    source_text = ast.unparse(functions["render_a5_execution"])
    if "turning_point_evidence" not in source_text or "render_assertion" not in source_text:
        _issue(issues, "ERROR", "A6_TURNING_POINT_EVIDENCE_NOT_RENDERED", "Turning-point evidence is not routed through assertion rendering")
    if not _function_calls(functions["render_assertion"], "render_evidence_ref"):
        _issue(issues, "ERROR", "A6_ASSERTION_EVIDENCE_CHAIN_BROKEN", "render_assertion does not call render_evidence_ref")
    if not _function_calls(functions["render_evidence_ref"], "render_result_evidence"):
        _issue(issues, "ERROR", "A6_MESSAGE_EVIDENCE_CHAIN_BROKEN", "render_evidence_ref does not call render_result_evidence")
    return issues


def validate_a6_contract(
    *,
    expected_memberships: Sequence[Mapping[str, Any]],
    actual_rows: Sequence[Mapping[str, Any]],
    packet: Mapping[str, Any],
    requested_selected_ids: Sequence[str],
    expected_message_sources: Sequence[Mapping[str, Any]] = (),
    actual_message_sources: Sequence[Mapping[str, Any]] = (),
    expected_attachments: Sequence[Mapping[str, Any]] = (),
    actual_attachments: Sequence[Mapping[str, Any]] = (),
    expected_attachment_sources: Sequence[Mapping[str, Any]] = (),
    actual_attachment_sources: Sequence[Mapping[str, Any]] = (),
    renderer_source: str | None = None,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    checks: dict[str, Any] = {}
    try:
        expected_by_membership = _rows_by_membership(expected_memberships)
        actual_by_membership = _rows_by_membership(actual_rows)
    except ValueError as exc:
        _issue(issues, "ERROR", "A6_MEMBERSHIP_ID_DUPLICATE", str(exc))
        return _finalize("A6", issues, checks)

    expected_ids = set(expected_by_membership)
    actual_ids = set(actual_by_membership)
    checks["expected_membership_count"] = len(expected_ids)
    checks["actual_membership_count"] = len(actual_ids)
    if expected_ids != actual_ids:
        _issue(issues, "ERROR", "A6_MEMBERSHIP_SET_MISMATCH", f"missing={sorted(expected_ids - actual_ids)}, extra={sorted(actual_ids - expected_ids)}")

    for membership_id, expected in expected_by_membership.items():
        actual = actual_by_membership.get(membership_id)
        if actual is None:
            continue
        for name in ("message_id", "conversation_id"):
            if str(actual.get(name)) != str(expected.get(name)):
                _issue(issues, "ERROR", "A6_MEMBERSHIP_IDENTITY_MISMATCH", f"membership {membership_id} {name}: expected {expected.get(name)!r}, got {actual.get(name)!r}")
        if bool(actual.get("timestamp_known")) != bool(expected.get("timestamp_known")):
            _issue(issues, "ERROR", "A6_TIMESTAMP_PRESERVATION_MISMATCH", f"membership {membership_id} timestamp-known state changed")

    selected_ids = [str(value) for value in packet.get("selected_message_ids") or []]
    requested = [str(value) for value in requested_selected_ids]
    if selected_ids != requested:
        _issue(issues, "ERROR", "A6_PACKET_SELECTION_MISMATCH", f"expected selected IDs {requested}, got {selected_ids}")
    if len(selected_ids) != len(set(selected_ids)):
        _issue(issues, "ERROR", "A6_PACKET_DUPLICATE_SELECTED_ID", "Packet selected_message_ids contains duplicates")
    packet_messages = list(packet.get("messages") or [])
    packet_ids = [str(row.get("message_id")) for row in packet_messages]
    if len(packet_ids) != len(set(packet_ids)):
        _issue(issues, "ERROR", "A6_PACKET_DUPLICATE_MESSAGE_ID", "Packet message IDs are not unique inside one conversation scope")
    conversations = {str(row.get("conversation_id")) for row in packet_messages}
    if len(conversations) != 1:
        _issue(issues, "ERROR", "A6_PACKET_CROSSES_CONVERSATIONS", f"Packet contains conversation IDs {sorted(conversations)}")
    selected_rows = [row for row in packet_messages if bool(row.get("selected"))]
    if {str(row.get("message_id")) for row in selected_rows} != set(selected_ids):
        _issue(issues, "ERROR", "A6_PACKET_SELECTED_FLAGS_MISMATCH", "selected=true rows do not equal selected_message_ids")
    if any(not str(row.get("membership_id") or "").strip() for row in packet_messages):
        _issue(issues, "ERROR", "A6_PACKET_MEMBERSHIP_ID_MISSING", "At least one packet message lacks membership_id")

    table_pairs = (
        ("message_sources", expected_message_sources, actual_message_sources),
        ("attachments", expected_attachments, actual_attachments),
        ("attachment_sources", expected_attachment_sources, actual_attachment_sources),
    )
    for name, expected, actual in table_pairs:
        if _canonical_rows(expected) != _canonical_rows(actual):
            _issue(issues, "ERROR", "A6_PROVENANCE_PROJECTION_MISMATCH", f"{name} rows differ from authoritative downstream view")
        checks[f"{name}_row_count"] = len(actual)

    if renderer_source is None:
        _issue(issues, "WARNING", "A6_RENDERER_NOT_CHECKED", "No A6 renderer source supplied for assertion-evidence routing audit")
    else:
        issues.extend(validate_a6_renderer_source(renderer_source))
        checks["renderer_evidence_chain_checked"] = True

    return _finalize("A6", issues, checks)


def aggregate_release_verdict(
    component_reports: Mapping[str, Mapping[str, Any] | None],
    *,
    job_results: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    components: dict[str, str] = {}
    jobs = dict(job_results or {})

    for name, report in component_reports.items():
        job_result = jobs.get(name)
        if job_result is not None and job_result != "success":
            components[name] = VERDICT_INVALID if job_result == "failure" else VERDICT_NEEDS_REVIEW
            _issue(issues, "ERROR" if job_result == "failure" else "WARNING", "A7_COMPONENT_JOB_NOT_SUCCESS", f"{name} job result is {job_result}")
            continue
        if report is None:
            components[name] = VERDICT_NEEDS_REVIEW
            _issue(issues, "WARNING", "A7_COMPONENT_REPORT_MISSING", f"{name} report is missing")
            continue
        verdict = str(report.get("verdict") or VERDICT_NEEDS_REVIEW)
        if verdict not in {VERDICT_VALID, VERDICT_INVALID, VERDICT_NEEDS_REVIEW}:
            verdict = VERDICT_NEEDS_REVIEW
            _issue(issues, "WARNING", "A7_COMPONENT_VERDICT_UNKNOWN", f"{name} report has unknown verdict")
        components[name] = verdict

    if any(value == VERDICT_INVALID for value in components.values()):
        overall = VERDICT_INVALID
    elif any(value == VERDICT_NEEDS_REVIEW for value in components.values()):
        overall = VERDICT_NEEDS_REVIEW
    else:
        overall = VERDICT_VALID
    return {
        "schema_version": 1,
        "overall_verdict": overall,
        "release_ready": overall == VERDICT_VALID,
        "components": components,
        "issues": issues,
    }
