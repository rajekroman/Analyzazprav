from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median
from typing import Any, Iterable, Mapping, Sequence


STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"


def _percentile(values: Sequence[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * p
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _source_message(record: Mapping[str, Any]) -> dict[str, Any]:
    required = ("message_id", "conversation_id", "session_id", "sequence_number")
    missing = [name for name in required if record.get(name) is None]
    if missing:
        raise ValueError(f"oracle source message is missing: {', '.join(missing)}")
    timestamp = record.get("timestamp_us")
    if timestamp is not None and (isinstance(timestamp, bool) or not isinstance(timestamp, int)):
        raise ValueError("timestamp_us must be int or null")
    word_count = record.get("word_count", 0)
    if isinstance(word_count, bool) or not isinstance(word_count, int) or word_count < 0:
        raise ValueError("word_count must be a non-negative integer")
    return {
        "message_id": int(record["message_id"]),
        "conversation_id": int(record["conversation_id"]),
        "session_id": int(record["session_id"]),
        "sequence_number": int(record["sequence_number"]),
        "participant_id": None if record.get("participant_id") is None else int(record["participant_id"]),
        "timestamp_us": timestamp,
        "word_count": word_count,
    }


def compute_a4_oracle(messages: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Independent small-data oracle for core A4 accounting metrics.

    This intentionally reimplements only simple, manually checkable rules from
    the published A4/A3 contract. It does not import or call A4 code.
    """

    source = [_source_message(record) for record in messages]
    if not source:
        raise ValueError("oracle requires at least one source message")
    conversation_ids = {record["conversation_id"] for record in source}
    if len(conversation_ids) != 1:
        raise ValueError("oracle expects exactly one conversation")
    ids = [record["message_id"] for record in source]
    duplicate_ids = [mid for mid, count in Counter(ids).items() if count > 1]
    if duplicate_ids:
        raise ValueError(f"duplicate message_id values: {duplicate_ids[:5]}")

    ordered = sorted(source, key=lambda row: (row["sequence_number"], row["message_id"]))
    turns: list[dict[str, Any]] = []
    batch: list[dict[str, Any]] = []

    def flush() -> None:
        if not batch:
            return
        turns.append(
            {
                "turn_id": len(turns) + 1,
                "conversation_id": batch[0]["conversation_id"],
                "session_id": batch[0]["session_id"],
                "participant_id": batch[0]["participant_id"],
                "start_us": batch[0]["timestamp_us"],
                "end_us": batch[-1]["timestamp_us"],
                "message_ids": tuple(row["message_id"] for row in batch),
                "message_count": len(batch),
                "word_count": sum(row["word_count"] for row in batch),
            }
        )

    for row in ordered:
        if not batch:
            batch = [row]
            continue
        if row["session_id"] == batch[-1]["session_id"] and row["participant_id"] == batch[-1]["participant_id"]:
            batch.append(row)
        else:
            flush()
            batch = [row]
    flush()

    responses: list[dict[str, Any]] = []
    for previous, current in zip(turns, turns[1:]):
        if previous["session_id"] != current["session_id"]:
            continue
        if previous["participant_id"] is None or current["participant_id"] is None:
            continue
        if previous["participant_id"] == current["participant_id"]:
            continue
        latency: float | None = None
        if previous["end_us"] is not None and current["start_us"] is not None:
            delta = current["start_us"] - previous["end_us"]
            if delta >= 0:
                latency = delta / 1_000_000
        responses.append(
            {
                "conversation_id": current["conversation_id"],
                "session_id": current["session_id"],
                "from_participant_id": previous["participant_id"],
                "responder_id": current["participant_id"],
                "previous_turn_id": previous["turn_id"],
                "response_turn_id": current["turn_id"],
                "latency_seconds": latency,
                "response_effort_ratio": current["word_count"] / max(1, previous["word_count"]),
            }
        )

    turn_count = Counter(turn["participant_id"] for turn in turns if turn["participant_id"] is not None)
    message_count = Counter(row["participant_id"] for row in source if row["participant_id"] is not None)
    initiations: Counter[int] = Counter()
    seen_sessions: set[int] = set()
    for turn in turns:
        if turn["session_id"] in seen_sessions:
            continue
        seen_sessions.add(turn["session_id"])
        if turn["participant_id"] is not None:
            initiations[int(turn["participant_id"])] += 1

    grouped_turns: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for turn in turns:
        grouped_turns[int(turn["session_id"])].append(turn)
    unanswered: Counter[int] = Counter()
    for session_turns in grouped_turns.values():
        for index, turn in enumerate(session_turns):
            pid = turn["participant_id"]
            if pid is None:
                continue
            answered = any(
                later["participant_id"] is not None and later["participant_id"] != pid
                for later in session_turns[index + 1 :]
            )
            if not answered:
                unanswered[int(pid)] += 1

    latency_by_responder: dict[int, list[float]] = defaultdict(list)
    response_count: Counter[int] = Counter()
    for sample in responses:
        pid = int(sample["responder_id"])
        response_count[pid] += 1
        if sample["latency_seconds"] is not None:
            latency_by_responder[pid].append(float(sample["latency_seconds"]))

    participants = sorted(message_count)
    participant_metrics: dict[int, dict[str, Any]] = {}
    for pid in participants:
        values = latency_by_responder.get(pid, [])
        participant_metrics[pid] = {
            "message_count": message_count[pid],
            "turn_count": turn_count[pid],
            "initiations": initiations[pid],
            "response_turn_count": response_count[pid],
            "latency_sample_count": len(values),
            "unanswered_turn_count": unanswered[pid],
            "median_response_latency_seconds": median(values) if values else None,
            "p25_response_latency_seconds": _percentile(values, 0.25),
            "p75_response_latency_seconds": _percentile(values, 0.75),
            "p90_response_latency_seconds": _percentile(values, 0.90),
        }

    return {
        "conversation_id": next(iter(conversation_ids)),
        "source_message_count": len(source),
        "known_sender_message_count": sum(row["participant_id"] is not None for row in source),
        "unknown_sender_message_count": sum(row["participant_id"] is None for row in source),
        "turn_count": len(turns),
        "session_count": len({row["session_id"] for row in source}),
        "turns": turns,
        "response_samples": responses,
        "participant_metrics": participant_metrics,
    }


def _metric_map(raw: Any) -> dict[int, Mapping[str, Any]]:
    if not isinstance(raw, Mapping):
        raise ValueError("participant_metrics must be an object")
    result: dict[int, Mapping[str, Any]] = {}
    for key, value in raw.items():
        if not isinstance(value, Mapping):
            raise ValueError(f"participant metric {key!r} must be an object")
        result[int(key)] = value
    return result


def validate_a4_against_oracle(
    source_messages: Iterable[Mapping[str, Any]],
    actual: Mapping[str, Any],
    *,
    tolerance: float = 1e-9,
) -> dict[str, Any]:
    expected = compute_a4_oracle(source_messages)
    issues: list[dict[str, str]] = []

    def fail(code: str, detail: str) -> None:
        issues.append({"severity": "ERROR", "code": code, "detail": detail})

    for key in (
        "conversation_id",
        "source_message_count",
        "known_sender_message_count",
        "unknown_sender_message_count",
        "turn_count",
        "session_count",
    ):
        if actual.get(key) != expected[key]:
            fail("A4_ORACLE_GLOBAL_MISMATCH", f"{key}: actual={actual.get(key)!r}, expected={expected[key]!r}")

    try:
        actual_metrics = _metric_map(actual.get("participant_metrics", {}))
    except (TypeError, ValueError) as exc:
        fail("A4_ORACLE_PARTICIPANT_SHAPE_INVALID", str(exc))
        actual_metrics = {}

    metric_names = (
        "message_count",
        "turn_count",
        "initiations",
        "response_turn_count",
        "latency_sample_count",
        "unanswered_turn_count",
        "median_response_latency_seconds",
        "p25_response_latency_seconds",
        "p75_response_latency_seconds",
        "p90_response_latency_seconds",
    )
    for pid, expected_metrics in expected["participant_metrics"].items():
        actual_row = actual_metrics.get(pid)
        if actual_row is None:
            fail("A4_ORACLE_PARTICIPANT_MISSING", f"participant {pid} missing")
            continue
        for name in metric_names:
            exp = expected_metrics[name]
            got = actual_row.get(name)
            if isinstance(exp, float):
                if got is None or abs(float(got) - exp) > tolerance:
                    fail("A4_ORACLE_METRIC_MISMATCH", f"participant {pid} {name}: actual={got!r}, expected={exp!r}")
            elif got != exp:
                fail("A4_ORACLE_METRIC_MISMATCH", f"participant {pid} {name}: actual={got!r}, expected={exp!r}")

    expected_turns = {
        int(turn["turn_id"]): (
            int(turn["session_id"]),
            turn["participant_id"],
            tuple(turn["message_ids"]),
            turn["start_us"],
            turn["end_us"],
        )
        for turn in expected["turns"]
    }
    actual_turns: dict[int, tuple[Any, ...]] = {}
    raw_turns = actual.get("turns", [])
    if not isinstance(raw_turns, (list, tuple)):
        fail("A4_ORACLE_TURNS_SHAPE_INVALID", "turns must be a list")
        raw_turns = []
    for turn in raw_turns:
        if not isinstance(turn, Mapping):
            fail("A4_ORACLE_TURNS_SHAPE_INVALID", "turn row must be an object")
            continue
        actual_turns[int(turn.get("turn_id"))] = (
            int(turn.get("session_id")),
            None if turn.get("participant_id") is None else int(turn.get("participant_id")),
            tuple(int(mid) for mid in (turn.get("message_ids") or [])),
            turn.get("start_us"),
            turn.get("end_us"),
        )
    if actual_turns != expected_turns:
        fail("A4_ORACLE_TURN_PARTITION_MISMATCH", "turn partition/session/sender/timestamps differ from oracle")

    def response_key(row: Mapping[str, Any]) -> tuple[int, int, int]:
        return (int(row["session_id"]), int(row["previous_turn_id"]), int(row["response_turn_id"]))

    expected_responses = {response_key(row): row for row in expected["response_samples"]}
    raw_responses = actual.get("response_samples", [])
    if not isinstance(raw_responses, (list, tuple)):
        fail("A4_ORACLE_RESPONSES_SHAPE_INVALID", "response_samples must be a list")
        raw_responses = []
    actual_responses = {
        response_key(row): row
        for row in raw_responses
        if isinstance(row, Mapping) and all(name in row for name in ("session_id", "previous_turn_id", "response_turn_id"))
    }
    if set(actual_responses) != set(expected_responses):
        fail("A4_ORACLE_RESPONSE_SET_MISMATCH", "response transition set differs from oracle")
    for key, exp in expected_responses.items():
        got = actual_responses.get(key)
        if got is None:
            continue
        if got.get("latency_seconds") is None or exp["latency_seconds"] is None:
            if got.get("latency_seconds") != exp["latency_seconds"]:
                fail("A4_ORACLE_LATENCY_MISMATCH", f"response {key} latency differs")
        elif abs(float(got["latency_seconds"]) - float(exp["latency_seconds"])) > tolerance:
            fail("A4_ORACLE_LATENCY_MISMATCH", f"response {key}: actual={got['latency_seconds']}, expected={exp['latency_seconds']}")
        if abs(float(got.get("response_effort_ratio")) - float(exp["response_effort_ratio"])) > tolerance:
            fail("A4_ORACLE_EFFORT_MISMATCH", f"response {key} effort ratio differs")

    return {
        "schema_version": 1,
        "status": STATUS_FAIL if issues else STATUS_PASS,
        "expected": expected,
        "counts": {"errors": len(issues)},
        "issues": issues,
    }
